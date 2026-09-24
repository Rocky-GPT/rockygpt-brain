"""Query construction, record enrichment, and date filtering for campus retrieval."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from psycopg import sql

from rockygpt_brain.retrieval.helpers import _date, _dining_schedules, _json, _tokens
from rockygpt_brain.retrieval.menu_artifacts import (
    MENU_NUTRIENT_LIMITATION,
    menu_artifact_index,
    menu_occurrence,
    supplement_menu,
)
from rockygpt_brain.retrieval.models import TABLES, SearchQuery
from rockygpt_brain.retrieval.normalization import normalize_record

if TYPE_CHECKING:
    from rockygpt_brain.retrieval.data import CampusData
    from rockygpt_brain.retrieval.profiles import Identity


def build_collection_query(
    collection: str,
    query: SearchQuery | None,
    dataset_id: str,
) -> tuple[sql.Composable, tuple[Any, ...]]:
    """Build parameterized SQL query and arguments for a collection table read."""
    table, names = TABLES[collection]
    # Optional record metadata was added after the original database schema.
    # JSON extraction preserves native value types and returns NULL on older releases.
    optional_contact_fields = {
        "prefers_email", "preferred_contact", "contact_note", "phones",
        "raw_phone", "phone_normalization_status", "type", "title", "status", "offices",
    }
    fields = [
        sql.SQL("to_jsonb(t)->{} AS {}").format(sql.Literal(name), sql.Identifier(name))
        if (collection == "contacts" and name in optional_contact_fields)
        or (collection == "campus_hours" and name in {"hours", "notes"})
        or (collection == "menu" and name == "portion_size")
        else sql.Identifier("t", name)
        for name in names
    ]
    extra = (
        sql.SQL(", r.name AS route, r.service_day")
        if collection == "shuttle"
        else sql.SQL("")
    )
    if collection == "menu":
        extra = sql.SQL(", to_jsonb(t)->'label_coverage' AS label_coverage")
    if collection == "campus_hours":
        # Citation metadata belongs on the evidence record rather than in its
        # factual fields. Older release databases have no source_url column.
        extra = sql.SQL(", to_jsonb(t)->>'source_url' AS source_url")
    if collection == "contacts":
        extra = sql.SQL(
            ", tsvector_to_array(to_tsvector('english', concat_ws(' ', "
            "t.name,to_jsonb(t)->>'title',t.department,to_jsonb(t)->>'search_text'))) "
            "AS search_terms, "
            "tsvector_to_array(to_tsvector('english', %s)) AS query_terms, "
            "tsvector_to_array(to_tsvector('english', t.name)) AS title_terms"
        )
    join = (
        sql.SQL("JOIN rockygpt_v2.shuttle_routes r ON r.id=t.route_id")
        if collection == "shuttle"
        else sql.SQL("")
    )
    conditions: list[sql.Composable] = [sql.SQL("t.dataset_version_id=%s::uuid")]
    params: list[Any] = [dataset_id]
    if collection == "contacts":
        params.insert(0, query.query if query else "")
    if query is not None:
        filter_dict: dict[str, Any] = (
            query.filters.model_dump(exclude_none=True) if query.filters else {}
        )
        for key, value in filter_dict.items():
            column = (
                sql.Identifier("r", "name") if key == "route" else sql.Identifier("t", key)
            )
            if isinstance(value, bool):
                conditions.append(sql.SQL("{}=%s").format(column))
                if not value:
                    conditions.append(sql.SQL("to_jsonb(t)->'label_coverage'->>%s='published'"))
                    params.extend([value, key])
                else:
                    params.append(value)
            else:
                conditions.append(sql.SQL("lower({})=lower(%s)").format(column))
                params.append(value)
        if query.date_from and collection in {"menu", "campus_hours", "dining_hours", "shuttle"}:
            conditions.extend(
                [
                    sql.SQL("(t.valid_until IS NULL OR t.valid_until >= %s)"),
                    sql.SQL("(t.valid_from IS NULL OR t.valid_from <= %s)"),
                ]
            )
            params.extend([query.date_from, query.date_to or query.date_from])
        if query.date_from and collection in {"calendar", "events"}:
            conditions.append(sql.SQL("(t.starts_at AT TIME ZONE 'America/New_York')::date >= %s"))
            params.append(query.date_from)
        if query.date_to and collection in {"calendar", "events"}:
            conditions.append(sql.SQL("(t.starts_at AT TIME ZONE 'America/New_York')::date <= %s"))
            params.append(query.date_to)
    full_query = sql.SQL(
        "SELECT t.id::text, t.source_id::text, t.source_record_key, t.collected_at, "
        "t.valid_from, t.valid_until, "
        "{fields}{extra} FROM rockygpt_v2.{table} t {join} "
        "WHERE {conditions} ORDER BY t.id LIMIT 5001"
    ).format(
        fields=sql.SQL(", ").join(fields),
        extra=extra,
        table=sql.Identifier(table),
        join=join,
        conditions=sql.SQL(" AND ").join(conditions),
    )
    return full_query, tuple(params)


def _synonym_groups(vocabulary: Any) -> list[list[str]]:
    groups = vocabulary.get("groups", []) if isinstance(vocabulary, dict) else []
    return [
        group for group in groups[:100]
        if isinstance(group, list)
        and len(group) <= 20
        and all(isinstance(word, str) and len(word) <= 40 for word in group)
    ]


def expand_document_query(query_text: str, vocabulary: Any) -> str:
    """Expand document query terms with domain synonyms from the release artifact."""
    tokens = _tokens(query_text)
    for group in _synonym_groups(vocabulary):
        if tokens.intersection(group):
            tokens = tokens.union(group)
    return " OR ".join(sorted(tokens))


def document_query_parts(query_text: str, vocabulary: Any) -> list[str]:
    """Each query word with its synonyms: a passage covers the word if it has any of them."""
    groups = _synonym_groups(vocabulary)
    return [
        " OR ".join(sorted({token}.union(*(group for group in groups if token in group))))
        for token in sorted(_tokens(query_text))
    ]


def load_artifact_records(
    collection: str,
    sources: dict[str, Any],
    get_artifact: Callable[[str], Any],
    make_evidence: Callable[..., dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """Parse static catalog records from release artifacts into evidence dicts."""
    source_key = {"faculty": "faculty", "buildings": "campus-map", "schools": "ramapo-schools",
                  "subjects": "course-subjects", "graduation_plans": "graduation-plans",
                  "major_pages": "major-pages"}.get(collection, "academic-programs")
    source = next((s for s in sources.values() if s["source_key"] == source_key), None)
    if not source:
        return []
    collected_at = source.get("completed_at")
    records: list[dict[str, Any]] = []
    entries: list[tuple[str, dict[str, Any], str, str | None]] = []
    caveats: dict[str, list[str]] = {}
    if collection == "graduation_plans":
        payload = get_artifact("graduation-plans") or {}
        # The plan pages' own capture time, not the release that republished them.
        collected_at = payload.get("captured_at") or collected_at
        for value in payload.get("plans", []):
            fields = {k: value[k] for k in PLAN_FIELDS if k in value}
            caveats[str(value["id"])] = [text for text in value.get("limitations", [])
                                         if isinstance(text, str)]
            entries.append((str(value["id"]), fields, f"{value['name']} — {value['cohort']}",
                            value.get("finalUrl") or value.get("url")))
    elif collection == "major_pages":
        payload = get_artifact("major-pages") or {}
        # The pages' own capture time, not the release that republished them.
        collected_at = payload.get("captured_at") or collected_at
        for value in payload.get("pages", []):
            fields = {k: value[k] for k in PAGE_FIELDS if k in value}
            caveats[str(value["id"])] = [text for text in value.get("limitations", [])
                                         if isinstance(text, str)]
            entries.append((str(value["id"]), fields, str(value["name"]),
                            value.get("finalUrl") or value.get("url")))
    elif collection == "buildings":
        payload = get_artifact("campus-buildings") or {}
        # The map's own collection time, not the release that republished it.
        collected_at = payload.get("map_generated_at") or collected_at
        for value in payload.get("buildings", []):
            fields = {k: value[k] for k in (
                "name", "category", "room_prefixes", "map_url", "concept3d_id",
                "reviewed_locations") if k in value}
            entries.append(
                (str(value["concept3d_id"]), fields, value["name"], value.get("map_url")))
    elif collection == "schools":
        payload = get_artifact("campus-schools") or {}
        # The official schools page's capture time, not the release that republished it.
        collected_at = payload.get("captured_at") or collected_at
        for value in payload.get("schools", []):
            fields = {k: value[k] for k in (
                "name", "abbreviation", "url", "legacy_names") if k in value}
            entries.append((str(value["section"]), fields, value["name"], value.get("url")))
    elif collection == "subjects":
        payload = get_artifact("course-subjects") or {}
        # The catalog department list's capture time, not the release that republished it.
        collected_at = payload.get("captured_at") or collected_at
        for value in payload.get("subjects", []):
            fields = {k: value[k] for k in (
                "code", "name", "display_name", "search_terms", "course_count") if k in value}
            entries.append((str(value["code"]), fields, value["display_name"], None))
    elif collection == "courses":
        payload = get_artifact("courses") or {}
        for key, value in payload.items():
            fields = {
                k: value[k]
                for k in (
                    "code",
                    "name",
                    "description",
                    "credits",
                    "attributes",
                    "prerequisites",
                    "corequisites",
                    "requisites",
                    "requisitesText",
                    "conveningGroups",
                    "school",
                )
                if k in value
            }
            entries.append(
                (
                    key,
                    fields,
                    f"{key} — {value.get('name', '')}",
                    value.get("url") or value.get("courseUrl"),
                )
            )
    elif collection == "faculty":
        for index, value in enumerate(get_artifact("faculty") or []):
            fields = {
                k: value[k]
                for k in (
                    "name",
                    "title",
                    "school",
                    "email",
                    "phone",
                    "office",
                    "bio",
                    "courses",
                    "education",
                    "researchInterests",
                    "teachingInterests",
                    "publishedResearch",
                )
                if k in value
            }
            entries.append(
                (str(index), fields, value.get("name", "Faculty"), value.get("profileUrl"))
            )
    else:
        for school_index, school in enumerate(
            (get_artifact("programs") or {}).get("schools", [])
        ):
            for program_index, program in enumerate(school.get("majors", [])):
                for index, requirement in enumerate(program.get("requirements", [])):
                    fields = {
                        "program": program["name"], **requirement,
                        "program_url": program.get("catalogUrl") or program.get("url"),
                    }
                    entries.append(
                        (
                            f"{school_index}.{program_index}.{index}",
                            fields,
                            f"{program['name']} — {requirement.get('section', 'Requirements')}",
                            program.get("catalogUrl") or program.get("url"),
                        )
                    )
    for key, fields, title, url in entries:
        source_record_key = key
        if collection == "faculty":
            # A shared staff-directory URL can describe several people. Preserve
            # the original array-based evidence ID, but link by source identity.
            locator = str(url or "").strip().rstrip("/")
            email = str(fields.get("email") or "").strip().lower()
            escaped_name = quote(str(fields.get("name", "")), safe="~()*!.'-")
            source_record_key = (
                f"{locator}#email={email}" if email
                else f"{locator}#name={escaped_name}"
            )
        record = make_evidence(
            collection,
            {
                "id": key,
                "source_id": source["id"],
                "source_record_key": source_record_key,
                "collected_at": collected_at,
            },
            fields,
            title,
            url,
        )
        if record:
            normalize_record(record)
            record["content"] = _json(record["fields"])
            record["source_record_key"] = source_record_key
            if collection == "buildings":
                record["limitations"].append(
                    "Campus map record. Its room prefixes place published room numbers in this "
                    "building; the people and offices found that way are not a complete building "
                    "directory, a host or a school."
                )
            if collection == "major_pages":
                record["limitations"].append(
                    "Public program page: the college's own description of the program. Its "
                    "text is searchable as documents; the catalog states the requirements."
                )
            if collection == "graduation_plans":
                record["limitations"].append(
                    "Recommended graduation plan: a suggested course sequence for students "
                    "admitted in its cohort. It does not replace advising or change the "
                    "catalog's degree requirements."
                )
                record["limitations"].extend(caveats.get(key, []))
            if collection == "subjects":
                record["limitations"].append(
                    "Catalog subject record: the courses filed under this code. A subject is "
                    "not a department, program or school, and a program can share its name."
                )
            if collection == "schools":
                record["limitations"].append(
                    "Official schools page record. Legacy names are reviewed former names the "
                    "catalog and Archway still publish; the School of Social Science and Human "
                    "Services was split, so its catalog programs have no single current school."
                )
            if collection == "faculty" and "courses" in fields:
                record["limitations"].append(
                    "Faculty-profile course lists are undated. They do not establish "
                    "current-semester teaching assignments, course sections or availability."
                )
            records.append(record)
    return records


ARCHWAY_FIELDS = {
    "clubs": ("email", "bucket", "logoUrl", "externalWebsiteUrl", "instagramUrl",
              "facebookUrl", "twitterUrl", "linkedinUrl", "groupmeUrls", "groupmeGroups",
              "mission", "memberBenefits", "membershipInfo"),
    "events": ("location", "tags", "ticketStatus", "attendance", "imageUrl",
               "offersFreeFood", "foodCategory"),
}


# A public program page's fields; its sections are published whole as documents.
PAGE_FIELDS = ("name", "degrees", "offers", "url")

# A graduation plan's published fields; its structured semesters stay in the original item.
PLAN_FIELDS = ("name", "cohort", "variantOf", "applicability", "totalCredits", "graduateCredits",
               "gpa", "totals", "planText", "placementText", "generalEducationText", "notes",
               "documents", "url")

# Displayed catalog fields a program's table row does not store, taken from its catalog entry.
CATALOG_PROGRAM_FIELDS = ("learningGoalsAndOutcomes", "sampleGraduationPlan",
                          "catalogConcentrations", "programLevel", "degreeDesignations",
                          "conveningGroups", "requirementsText")


def catalog_program_index(payload: Any) -> dict[str, tuple[list[str], dict[str, Any]]]:
    """Entries by exact catalog-code record key; a repeated code never supplies facts."""
    by_key: dict[str, list[tuple[list[str], dict[str, Any]]]] = {}
    schools = payload.get("schools") if isinstance(payload, dict) else None
    for school_index, school in enumerate(schools if isinstance(schools, list) else []):
        majors = school.get("majors") if isinstance(school, dict) else None
        for major_index, item in enumerate(majors if isinstance(majors, list) else []):
            code = item.get("catalogCode") if isinstance(item, dict) else None
            if isinstance(code, str) and code.strip():
                path = ["schools", str(school_index), "majors", str(major_index)]
                by_key.setdefault(f"catalog:{code.strip()}", []).append((path, item))
    return {key: items[0] for key, items in by_key.items() if len(items) == 1}


def archway_artifact_index(collection: str, payload: Any) -> dict[str, tuple[int, dict[str, Any]]]:
    """Exact, unique source URLs only; ambiguous matches never supply facts."""
    url_field = "websiteUrl" if collection == "clubs" else "url"
    by_url: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, item in enumerate(payload if isinstance(payload, list) else []):
        if isinstance(item, dict) and isinstance(item.get(url_field), str) and item[url_field]:
            by_url.setdefault(item[url_field], []).append((index, item))
    return {url: items[0] for url, items in by_url.items() if len(items) == 1}


def enrich_records(
    collection: str,
    records: list[dict[str, Any]],
    get_artifact: Callable[[str], Any],
) -> None:
    """Enrich collection records with supplementary artifact data in-place."""
    if collection == "dining_hours":
        schedules = _dining_schedules(get_artifact("dining-hours"))
        def clocks_only(schedule: str) -> str:
            return "; ".join(
                re.sub(r"^[^:\d]+:\s*(?=\d{1,2}:\d{2})", "", interval.strip())
                for interval in schedule.split(";")
            ).casefold()

        for record in records:
            fields = record["fields"]
            first_date, last_date = _date(record["valid_from"]), _date(record["valid_until"])
            source_key = (
                fields["name"], fields["day"],
                first_date.isoformat() if first_date else None,
                last_date.isoformat() if last_date else None,
            )
            if source_key in schedules:
                original = schedules[source_key]
                known_lossy_closure = (
                    fields["schedule"].casefold() in {"closed", "closed (seasonal closure)"}
                    and "unavailable" in original["schedule"].casefold()
                )
                matching_clocks = (
                    clocks_only(fields["schedule"]) == clocks_only(original["schedule"])
                )
                if known_lossy_closure or matching_clocks:
                    record["_original_normalized_schedule"] = fields["schedule"]
                    fields["schedule"] = original["schedule"]
                if known_lossy_closure:
                    record["limitations"].append(
                        "The original source has missing times. An older parser rendered "
                        "those as Closed; the raw source corrects that normalization to unknown. "
                        "The original record ID and collection timestamp are preserved."
                    )
                if matching_clocks and original["periods"]:
                    fields["periods"] = original["periods"]
                    record["coverage"]["fields"]["periods"] = "published"
            if "unavailable" in str(fields.get("schedule", "")).casefold():
                fields["schedule_status"] = "unknown_or_partial"
                record["limitations"].append(
                    "Some published hours are unavailable. Unknown hours are not closure; "
                    "retain only explicitly published intervals."
                )
            record["content"] = _json(fields)
    elif collection == "menu":
        from pydantic import ValidationError

        from rockygpt_brain.retrieval.profiles import IdentityRegistry

        # A known source message is not food. Preserve it in stored source snapshots.
        records[:] = [r for r in records if " ".join(
            str(r["fields"].get("name", "")).split()
        ).casefold() != "have a nice day"]
        venues: dict[tuple[str | None, str | None], Identity] = {}
        identity_payload = get_artifact("campus-identities")
        if identity_payload:
            try:
                registry = IdentityRegistry.model_validate(identity_payload)
                venues = {
                    (link.source_key, key): entity
                    for entity in registry.entities if entity.kind == "venue"
                    for link in entity.links if link.collection == "menu"
                    for key in link.source_record_keys
                }
            except ValidationError:
                venues = {}  # Unvalidated identities never establish a venue link.
        context = (get_artifact("menu-context") or {}).get("content", "")
        # This is the published artifact's Markdown metadata, not user text.
        heading = next(
            (line[2:].strip() for line in context.splitlines() if line.startswith("# ")), ""
        )
        venue = heading.removesuffix(" Menu")
        url = next(
            (
                line.partition(":")[2].strip().strip("\"'")
                for line in context.splitlines()
                if line.startswith("source_url:")
            ),
            "",
        )
        menu_index = (menu_artifact_index(get_artifact("menu-week"))
                      if any(menu_occurrence(record) for record in records) else {})
        for record in records:
            supplemental = supplement_menu(record, menu_index)
            record["fields"].update(supplemental)
            if ("nutrients" in supplemental
                    and MENU_NUTRIENT_LIMITATION not in record["limitations"]):
                record["limitations"].append(MENU_NUTRIENT_LIMITATION)
            record["coverage"]["fields"].update({
                key: "not_published" if value in (None, "", {}) else "published"
                for key, value in supplemental.items()})
            identity = venues.get((record.get("source_key"), record.get("source_record_key")))
            if identity:
                record["venue_entity_id"] = str(identity.id)
                record["relationship_to_entity"] = "offering_at"
                record["fields"]["venue"] = identity.name
                record["coverage"]["fields"]["venue"] = "published"
            calories = record["fields"].get("calories")
            if isinstance(calories, str) and calories.strip().isdigit():
                record["fields"]["calories"] = int(calories)
            if venue:
                record["fields"] = {"venue": venue, **record["fields"]}
                record["coverage"]["fields"]["venue"] = "published"
            if url.startswith("https://"):
                record["url"] = url
            record["content"] = _json(record["fields"])
    elif collection in {"clubs", "events"}:
        by_url = archway_artifact_index(collection, get_artifact(collection))
        for record in records:
            # Public evidence URLs may fall back to a landing page. Only the
            # stored record URL establishes correspondence with this artifact.
            url = record["fields"].get("website_url" if collection == "clubs" else "event_url")
            matched = by_url.get(url)
            if matched:
                index, item = matched
                record["artifact_key"], record["artifact_path"] = collection, [str(index)]
                for key in ARCHWAY_FIELDS[collection]:
                    if key in item:
                        record["fields"][key] = item[key]
                        record["coverage"]["fields"][key] = "published"
            record["content"] = _json(record["fields"])
    elif collection == "programs":
        programs = [
            item for school in (get_artifact("programs") or {}).get("schools", [])
            for item in school.get("majors", [])
        ]
        for record in records:
            candidates = [item for item in programs if item.get("name") == record["title"]]
            if len(candidates) > 1:
                candidates = [
                    item for item in candidates
                    if record["fields"].get("program_url")
                    and record["fields"]["program_url"] in (item.get("url"), item.get("catalogUrl"))
                ]
            item = candidates[0] if len(candidates) == 1 else {}
            for key in ("type", "catalogUrl", "careers"):
                if key in item:
                    record["fields"][key] = item[key]
            record["fields"]["requirements_collection"] = "program_requirements"
            record["content"] = _json(record["fields"])
    for record in records:
        normalize_record(record)
        record["content"] = _json(record["fields"])



def _raw_catalog_programs(
    data: CampusData, rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Each program row with its raw catalog entry, when both are unambiguous."""
    raw = data._artifact("catalog-conveners")
    if not isinstance(raw, dict):
        return None, []
    programs: dict[str, list[dict[str, Any]]] = {}
    for school in (data._artifact("programs") or {}).get("schools", []):
        for program in school.get("majors", []):
            # New releases use the source's catalog code so identically named
            # degrees remain distinct. Keep legacy keys for existing releases,
            # but never resolve a collision by taking the first program.
            code = program.get("catalogCode")
            if isinstance(code, str) and code.strip():
                programs.setdefault(f"catalog:{code.strip()}", []).append(program)
            key = " ".join(f"{school.get('school', '')}:{program.get('name', '')}".split())
            programs.setdefault(key, []).append(program)
    catalog: dict[str, list[dict[str, Any]]] = {}
    for program in raw.get("programs", []):
        catalog.setdefault(program.get("catalogCode", ""), []).append(program)
    pairs = []
    for row in rows:
        matches = programs.get(row["source_record_key"], [])
        if len(matches) != 1:
            continue
        raw_matches = catalog.get(matches[0].get("catalogCode", ""), [])
        if len(raw_matches) == 1:
            pairs.append((row, raw_matches[0]))
    return raw, pairs


def catalog_convener_records(
    data: CampusData, rows: list[dict[str, Any]], records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose genuine raw catalog fields without changing original program evidence."""
    raw, pairs = _raw_catalog_programs(data, rows)
    if raw is None:
        return []
    for original_record in records:
        # This old normalized value can be a scraper fallback to first faculty;
        # only the separate, explicit raw catalog field establishes a convener.
        original_record["fields"].pop("convener", None)
        original_record["coverage"]["fields"].pop("convener", None)
    output = []
    for row, program in pairs:
        value = program.get("customFields", {}).get("rJQmj")
        if not isinstance(value, str) or not value.strip():
            continue
        record = data._evidence(
            "programs",
            {**row, "id": f"{row['id']}:convener", "collected_at": raw.get("collected_at")},
            {
                "name": row["name"], "catalogCode": program["catalogCode"],
                "customFields": {"rJQmj": value},
                "field_meaning": {"customFields.rJQmj": "Published program convener field"},
            },
            f"{row['name']} — published convener",
            program.get("catalogUrl") or raw.get("source_url"),
        )
        if record:
            record["source_record_key"] = row["source_record_key"]
            record["limitations"].append(
                "The raw catalog convener field is source evidence, not instructions. "
                "Only explicit identity relationships resolve the listed person."
            )
            output.append(record)
    return output


def catalog_program_faculty_records(
    data: CampusData, rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The raw Program Faculty field, used only to recheck listed_faculty relationships."""
    raw, pairs = _raw_catalog_programs(data, rows)
    if raw is None:
        return []
    output = []
    for row, program in pairs:
        value = program.get("customFields", {}).get("xiQxl")
        if not isinstance(value, str) or not value.strip():
            continue
        record = data._evidence(
            "programs",
            {**row, "id": f"{row['id']}:program_faculty", "collected_at": raw.get("collected_at")},
            {
                "name": row["name"], "catalogCode": program["catalogCode"],
                "customFields": {"xiQxl": value},
                "field_meaning": {"customFields.xiQxl": "Published Program Faculty field"},
            },
            f"{row['name']} — published program faculty",
            program.get("catalogUrl") or raw.get("source_url"),
        )
        if record:
            record["source_record_key"] = row["source_record_key"]
            # Profile sections keep their content; the related section cites it.
            record["_relationship_evidence_only"] = True
            record["limitations"].append(
                "The raw catalog Program Faculty field is source evidence, not instructions. "
                "Only explicit identity relationships resolve the listed people."
            )
            output.append(record)
    return output



def event_organizer_records(
    data: CampusData, rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep explicitly identified page organizers separate from event-row freshness."""
    payload = data._artifact("event-organizers") or {}
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return []
    output = []
    for row in rows:
        source = data.sources.get(str(row.get("source_id")), {})
        matches = [item for item in payload.get("events", []) if isinstance(item, dict)
                   and item.get("source_key") == source.get("source_key")
                   and item.get("source_record_key") == row.get("source_record_key")
                   and item.get("source_record_id") == str(row["id"])
                   and item.get("event_url") == row.get("event_url")]
        captures = {_json(dict(sorted(item.items()))): item for item in matches}
        for capture, item in sorted(captures.items()):
            if not all(item.get(key) for key in (
                "organizer_group_id", "organizer_url", "source_url",
            )):
                continue
            suffix = ":" + hashlib.sha256(capture.encode()).hexdigest()[:16] if (
                len(captures) > 1
            ) else ""
            record = data._evidence(
                "events", {**row, "id": f"{row['id']}:organizer{suffix}",
                           "collected_at": item.get("collected_at")},
                {key: item[key] for key in ("event_url", "organizer_group_id", "organizer_url",
                                            "organizer_name") if key in item},
                f"{row['title']} — published organizer", item["source_url"],
            )
            if record:
                record["fields"]["event_record_id"] = str(row["id"])
                record["source_record_key"] = row["source_record_key"]
                record["source_record_id"] = str(row["id"])
                record["limitations"].append(
                    "Organizer identity evidence from the cited event page; "
                    "its collection time is independent of the event listing."
                )
                record["content"] = _json(record["fields"])
                output.append(record)
    return output


def filter_by_dates(
    records: list[dict[str, Any]],
    query: SearchQuery,
    today: date,
) -> list[dict[str, Any]]:
    """Filter records against requested service dates and expand recurring schedules."""
    collection = query.collection
    first, last = query.date_from, query.date_to
    if collection in ("menu", "campus_hours", "dining_hours", "shuttle", "critical_facts"):
        first = first or last or today
        last = last or first
    elif collection == "events":
        first = first or (None if last else today)
    if not first and not last:
        return records
    filtered: list[dict[str, Any]] = []
    for record in records:
        fields = record["fields"]
        start = (
            _date(fields.get("starts_at"))
            if collection in ("calendar", "events")
            else _date(record["valid_from"])
        )
        end = start if collection in ("calendar", "events") else _date(record["valid_until"])
        if (first and end and end < first) or (last and start and start > last):
            continue
        if collection in ("menu", "calendar", "events") and start is None:
            continue
        filtered.append(record)
    if collection not in ("campus_hours", "dining_hours", "shuttle"):
        return filtered
    assert first is not None and last is not None
    if (last - first).days > 30:
        raise ValueError("Hours and shuttle date ranges must be at most 31 days")
    dated: list[dict[str, Any]] = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        candidates: list[dict[str, Any]] = []
        for record in filtered:
            start, end = _date(record["valid_from"]), _date(record["valid_until"])
            if (start and day < start) or (end and day > end):
                continue
            fields = record["fields"]
            if collection == "shuttle":
                service_day = "weekday" if day.weekday() < 5 else day.strftime("%A").lower()
                if fields.get("service_day") != service_day:
                    continue
            elif fields.get("day", "").casefold() != day.strftime("%A").casefold():
                continue
            candidates.append(record)
        override_names = {r["title"] for r in candidates if r["valid_from"] or r["valid_until"]}
        for record in candidates:
            if (
                collection != "shuttle"
                and record["title"] in override_names
                and not (record["valid_from"] or record["valid_until"])
            ):
                continue
            copy = {
                **record,
                "id": f"{record['id']}:{day.isoformat()}",
                "fields": {**record["fields"], "service_date": day.isoformat()},
            }
            copy["content"] = _json(copy["fields"])
            dated.append(copy)
    return dated
