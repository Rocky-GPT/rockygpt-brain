"""Query construction, record enrichment, and date filtering for campus retrieval."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from psycopg import sql

from rockygpt_brain.retrieval.helpers import _date, _dining_periods, _json, _tokens
from rockygpt_brain.retrieval.models import TABLES, SearchQuery


def build_collection_query(
    collection: str,
    query: SearchQuery | None,
    dataset_id: str,
) -> tuple[sql.Composable, tuple[Any, ...]]:
    """Build parameterized SQL query and arguments for a collection table read."""
    table, names = TABLES[collection]
    # Optional contact metadata was added after the original directory schema.
    # JSON extraction preserves native value types and returns NULL on older releases.
    optional_contact_fields = {
        "prefers_email", "preferred_contact", "contact_note", "phones",
        "raw_phone", "phone_normalization_status", "type", "title", "status", "offices",
    }
    fields = [
        sql.SQL("to_jsonb(t)->{} AS {}").format(sql.Literal(name), sql.Identifier(name))
        if collection == "contacts" and name in optional_contact_fields
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
    if collection == "contacts":
        extra = sql.SQL(
            ", tsvector_to_array(to_tsvector('english', concat_ws(' ', "
            "t.name,to_jsonb(t)->>'title',t.department,to_jsonb(t)->>'search_text'))) AS search_terms, "
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
        filter_dict: dict[str, Any] = query.filters.model_dump(exclude_none=True) if query.filters else {}
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


def expand_document_query(query_text: str, vocabulary: Any) -> str:
    """Expand document query terms with domain synonyms from the release artifact."""
    tokens = _tokens(query_text)
    groups = vocabulary.get("groups", []) if isinstance(vocabulary, dict) else []
    for group in groups[:100]:
        if (
            isinstance(group, list)
            and len(group) <= 20
            and all(isinstance(word, str) and len(word) <= 40 for word in group)
            and tokens.intersection(group)
        ):
            tokens = tokens.union(group)
    return " OR ".join(sorted(tokens))


def load_artifact_records(
    collection: str,
    sources: dict[str, Any],
    get_artifact: Callable[[str], Any],
    make_evidence: Callable[..., dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """Parse static catalog records from release artifacts into evidence dicts."""
    source_key = "faculty" if collection == "faculty" else "academic-programs"
    source = next((s for s in sources.values() if s["source_key"] == source_key), None)
    if not source:
        return []
    records: list[dict[str, Any]] = []
    entries: list[tuple[str, dict[str, Any], str, str | None]] = []
    if collection == "courses":
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
                    fields = {"program": program["name"], **requirement}
                    entries.append(
                        (
                            f"{school_index}.{program_index}.{index}",
                            fields,
                            f"{program['name']} — {requirement.get('section', 'Requirements')}",
                            program.get("catalogUrl") or program.get("url"),
                        )
                    )
    for key, fields, title, url in entries:
        record = make_evidence(
            collection,
            {
                "id": key,
                "source_id": source["id"],
                "collected_at": source.get("completed_at"),
            },
            fields,
            title,
            url,
        )
        if record:
            records.append(record)
    return records


def enrich_records(
    collection: str,
    records: list[dict[str, Any]],
    get_artifact: Callable[[str], Any],
) -> None:
    """Enrich collection records with supplementary artifact data in-place."""
    if collection == "dining_hours":
        periods = _dining_periods(get_artifact("dining-hours"))
        for record in records:
            fields = record["fields"]
            schedule_key = (
                fields["name"],
                fields["day"],
                record["valid_from"],
                record["valid_until"],
                fields["schedule"],
            )
            if schedule_key in periods:
                fields["periods"] = periods[schedule_key]
                record["coverage"]["fields"]["periods"] = "published"
                record["content"] = _json(fields)
    elif collection == "menu":
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
        for record in records:
            if venue:
                record["fields"] = {"venue": venue, **record["fields"]}
                record["coverage"]["fields"]["venue"] = "published"
            if url.startswith("https://"):
                record["url"] = url
            record["content"] = _json(record["fields"])
    elif collection == "events":
        by_url = {item.get("url"): item for item in get_artifact("events") or []}
        for record in records:
            item = by_url.get(record["url"], {})
            for key in ("location", "tags", "ticketStatus"):
                if key in item:
                    record["fields"][key] = item[key]
            record["content"] = _json(record["fields"])
    elif collection == "programs":
        programs = {
            item["name"]: item
            for school in (get_artifact("programs") or {}).get("schools", [])
            for item in school.get("majors", [])
        }
        for record in records:
            item = programs.get(record["title"], {})
            for key in ("type", "convener", "catalogUrl", "careers"):
                if key in item:
                    record["fields"][key] = item[key]
            record["fields"]["requirements_collection"] = "program_requirements"
            record["content"] = _json(record["fields"])


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
