"""Resolve curated identities and retrieve their independently sourced records."""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from datetime import date as CalendarDate
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast
from uuid import UUID

from psycopg import sql
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from rockygpt_brain.retrieval.helpers import _date, _instant, _json
from rockygpt_brain.retrieval.models import CAMPUS_ZONE, TABLES, Collection, SearchQuery
from rockygpt_brain.retrieval.processing import (
    catalog_convener_records,
    catalog_program_faculty_records,
    event_organizer_records,
)
from rockygpt_brain.retrieval.subjects import course_subject

if TYPE_CHECKING:
    from rockygpt_brain.retrieval.data import CampusData


def _trimmed(value: str) -> str:
    if value != value.strip() or not value.strip():
        raise ValueError("Identity text must be nonempty and already trimmed")
    return value


IdentityText = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=240), AfterValidator(_trimmed)
]
RecordKey = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=500), AfterValidator(_trimmed)
]

ProfileSection = Literal[
    "contact", "hours", "faculty", "courses", "program", "conveners", "menu", "club", "event",
    "related", "requirements", "building", "school", "subject", "graduation_plans",
]
RelationshipType = Literal[
    "convener", "listed_faculty", "profile_course", "organized_by", "office_at", "located_at",
    "part_of", "includes_course",
]
# A published room places its holder in the building that owns the room's prefix.
ROOM_RELATIONSHIPS = frozenset({"office_at", "located_at"})
ROOM = re.compile(r"([A-Z]+)-\d{1,4}[A-Z]?")
# Archway directory groups: student clubs and other campus organizations.
ARCHWAY_GROUPS = frozenset({"club", "organization"})
LinkCollection = Literal[
    "contacts", "campus_hours", "dining_hours", "menu", "faculty", "programs", "courses",
    "clubs", "events", "buildings", "schools", "subjects", "graduation_plans", "major_pages",
]
SECTION_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "contact": ("contacts", "faculty", "clubs"),
    "hours": ("campus_hours", "dining_hours"),
    "faculty": ("faculty",),
    "courses": ("faculty",),
    "program": ("programs", "major_pages"),
    "conveners": ("programs",),
    "menu": ("menu",),
    "club": ("clubs",),
    "event": ("events",),
    "related": (),  # Published relationships, not linked source records.
    "requirements": (),  # Published requirement groups, not linked source records.
    "building": ("buildings",),
    "school": ("schools",),
    "subject": ("subjects",),
    "graduation_plans": ("graduation_plans",),
}


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def lookup_terms(entity: Identity) -> set[str]:
    """What a lookup by name compares: the normalized name and every normalized alias."""
    return {_normalize(value) for value in [entity.name, *entity.aliases]}


# A leading article or campus name ("the Ramapo library") names nothing the rest doesn't.
CAMPUS_PREFIX = re.compile(
    r"(?:the )?(?:(?:ramapo(?: college)?(?: of new jersey)?|rcnj)(?:'s)? )?", re.IGNORECASE,
)


def _without_campus_prefix(value: str) -> str | None:
    """The rest of a name after a leading article or campus name, if it has one."""
    text = " ".join(value.replace("’", "'").split())
    prefix = CAMPUS_PREFIX.match(text)
    rest = text[prefix.end():] if prefix else text
    return rest if rest and rest != text else None


class ProfileQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str | None = Field(default=None, min_length=1, max_length=240)
    entity_id: UUID | None = None
    include: list[ProfileSection] = Field(min_length=1, max_length=15)
    date: CalendarDate | None = Field(
        default=None, description=(
            "Campus-local service date; null means today for hours/menu. An event is a dated "
            "occurrence: null retains its published date; an explicit date restricts occurrences."
        ),
    )
    meal: str | None = Field(
        default=None, min_length=1, max_length=80,
        description="Published meal label for menu and dining periods, or null for all meals.",
    )
    menu_limit: int = Field(default=12, ge=1, le=100, description="Maximum menu item records.")
    relationship: RelationshipType | None = Field(
        default=None,
        description="With include=['related']: only this relationship type, or null for all.",
    )
    direction: Literal["outgoing", "incoming"] | None = Field(
        default=None,
        description=(
            "With include=['related']: relationships this entity declares (outgoing), ones "
            "that point to it (incoming), or null for both."
        ),
    )
    cohort: str | None = Field(
        default=None, min_length=1, max_length=40,
        description="Admission cohort as published, e.g. 'Fall 2024'.",
    )
    plan: str | None = Field(
        default=None, min_length=1,
        description="A plan name, or its distinctive words such as 'Data Science 4+1'.",
    )
    diet: Literal["vegan", "vegetarian"] | None = Field(
        default=None, description="Only menu items labeled with this diet.",
    )

    @model_validator(mode="after")
    def valid_selector(self) -> ProfileQuery:
        if (self.entity is None) == (self.entity_id is None):
            raise ValueError("Supply exactly one published name/alias or persistent entity_id")
        if self.entity is not None and not self.entity.strip():
            raise ValueError("entity must not be blank")
        if len(set(self.include)) != len(self.include):
            raise ValueError("include must not contain duplicates")
        if self.meal is not None and not self.meal.strip():
            raise ValueError("meal must not be blank")
        if (self.relationship or self.direction) and "related" not in self.include:
            raise ValueError("relationship and direction apply only to the related section")
        if self.cohort is not None and not self.cohort.strip():
            raise ValueError("cohort must not be blank")
        if self.cohort is not None and "graduation_plans" not in self.include:
            raise ValueError("cohort applies only to the graduation_plans section")
        if self.plan is not None and not self.plan.strip():
            raise ValueError("plan must not be blank")
        if self.plan is not None and "graduation_plans" not in self.include:
            raise ValueError("plan applies only to the graduation_plans section")
        if self.diet is not None and "menu" not in self.include:
            raise ValueError("diet applies only to the menu section")
        return self


class IdentityLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection: LinkCollection
    source_key: RecordKey
    source_record_keys: list[RecordKey] = Field(min_length=1, max_length=25000)
    source_record_ids: list[RecordKey] | None = Field(default=None, min_length=1, max_length=25000)


class RecordReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection: LinkCollection
    source_key: RecordKey
    source_record_key: RecordKey
    source_record_id: RecordKey | None = None


class RelationshipEvidence(RecordReference):
    field: RecordKey
    source_url: RecordKey | None = None


class IdentityRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: RelationshipType
    target_entity_id: UUID | None = None
    target_record: RecordReference | None = None
    evidence: list[RelationshipEvidence] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def target_matches_type(self) -> IdentityRelationship:
        if self.type in {"convener", "listed_faculty", "organized_by", "part_of",
                         *ROOM_RELATIONSHIPS}:
            if self.target_entity_id is None or self.target_record is not None:
                raise ValueError("This relationship targets a persistent identity")
            if self.type == "organized_by" and any(
                reference.collection != "events" or reference.field != "organizer_group_id"
                or reference.source_record_id is None for reference in self.evidence
            ):
                raise ValueError("An organizer relationship requires explicit event-page IDs")
            if self.type == "listed_faculty" and any(
                reference.collection != "programs" or reference.field != "customFields.xiQxl"
                for reference in self.evidence
            ):
                raise ValueError("A listing requires the catalog Program Faculty field")
            if self.type in ROOM_RELATIONSHIPS and any(
                (reference.collection, reference.field) != ("contacts", "office")
                and (self.type, reference.collection, reference.field)
                != ("located_at", "buildings", "reviewed_locations")
                for reference in self.evidence
            ):
                raise ValueError("A room relationship requires a contact's published office "
                                 "or, for an office, the building's reviewed statement")
            if self.type == "part_of" and any(
                reference.collection not in {"programs", "faculty"} or reference.field != "school"
                for reference in self.evidence
            ):
                raise ValueError("A school placement requires a published school field")
        elif self.target_record is None or self.target_entity_id is not None:
            raise ValueError("A course relationship targets a catalog record")
        elif self.target_record.collection != "courses":
            raise ValueError("A course relationship's target must be a catalog course")
        elif self.type == "includes_course" and any(
            reference.collection != "courses" or reference.field != "code"
            for reference in self.evidence
        ):
            raise ValueError("A subject's course is established by the course's own code")
        return self


class IdentityStatus(BaseModel):
    """A status the identity's own records publish, with the fields that publish it."""

    model_config = ConfigDict(extra="forbid")
    state: Literal["retired"]
    evidence: list[RelationshipEvidence] = Field(min_length=1, max_length=32)


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal[
        "office", "person", "facility", "venue", "program", "club", "organization", "event",
        "building", "school", "subject",
    ]
    name: IdentityText
    aliases: list[IdentityText] = Field(max_length=32)
    links: list[IdentityLink] = Field(min_length=1, max_length=32)
    relationships: list[IdentityRelationship] = Field(default_factory=list, max_length=1000)
    status: IdentityStatus | None = None

    @field_validator("id", mode="before")
    @classmethod
    def canonical_id(cls, value: Any) -> Any:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("Identity ID must be a lowercase canonical UUID")
        return value


class IdentityRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    entities: list[Identity] = Field(max_length=5000)

    @field_validator("schema_version", mode="before")
    @classmethod
    def numeric_version(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("schema_version must be a number")
        return value

    @model_validator(mode="after")
    def unique_ids(self) -> IdentityRegistry:
        if len({entity.id for entity in self.entities}) != len(self.entities):
            raise ValueError("Duplicate persistent entity ID")
        owners: dict[tuple[str, str, str], list[set[str] | None]] = {}
        row_owners: set[tuple[str, str, str]] = set()
        for entity in self.entities:
            for link in entity.links:
                row_ids = set(link.source_record_ids) if link.source_record_ids else None
                for row_id in link.source_record_ids or []:
                    reference = (link.collection, link.source_key, row_id)
                    if reference in row_owners:
                        raise ValueError("Source record IDs must not be duplicated")
                    row_owners.add(reference)
                for key in link.source_record_keys:
                    reference = (link.collection, link.source_key, key)
                    previous = owners.setdefault(reference, [])
                    if any(row_ids is None or ids is None or row_ids.intersection(ids)
                           for ids in previous):
                        raise ValueError("Source record links must not be duplicated")
                    previous.append(row_ids)
        return self


def _identity_summary(entity: Identity) -> dict[str, str]:
    summary = {"id": str(entity.id), "name": entity.name, "kind": entity.kind}
    if entity.status is not None:
        # Wherever the entity is named, e.g. as a program's convener, it is shown as retired.
        summary["status"] = entity.status.state
    return summary


def _comparison_key(field: str, value: Any) -> str:
    """Compare narrow presentation variants without changing published values."""
    if isinstance(value, str):
        if field == "phone" and re.fullmatch(r"\+?[0-9().\s-]+", value.strip()):
            digits = re.sub(r"\D", "", value)
            if len(digits) == 11 and digits.startswith("1"):
                digits = digits[1:]
            elif "+" in value:
                return _json(value)
            if len(digits) == 10:
                return _json({"us_phone_digits": digits})
        if field == "office":
            room = re.fullmatch(r"([A-Za-z]{1,6})[ -]?(\d{2,4}[A-Za-z]?)", value.strip())
            if room:
                return _json({"building_room": (room[1], room[2])})
    return _json(value)


def _field_coverage(
    records: list[dict[str, Any]], fields: tuple[str, ...]
) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
    coverage: dict[str, str] = {}
    conflicts: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        values: dict[str, dict[str, Any]] = {}
        for record in records:
            value = record["fields"].get(field)
            if value is None or value == "":
                continue
            key = _comparison_key(field, value)
            values.setdefault(key, {"value": value, "evidence_ids": []})["evidence_ids"].append(
                record["id"]
            )
        coverage[field] = (
            "conflict" if len(values) > 1 else "published" if values else "not_published"
        )
        if len(values) > 1:
            conflicts[field] = list(values.values())
    return coverage, conflicts


def _select_fields(record: dict[str, Any], fields: tuple[str, ...]) -> None:
    record["fields"] = {key: value for key, value in record["fields"].items() if key in fields}
    record["coverage"]["fields"] = {
        key: value for key, value in record["coverage"]["fields"].items() if key in fields
    }


def _linked_records(
    data: CampusData, link: IdentityLink,
) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Read only exact release-validated references, including artifact-backed records."""
    if link.collection in {"faculty", "courses", "buildings", "schools", "subjects",
                           "graduation_plans", "major_pages"}:
        records = [
            deepcopy(record) for record in data._load(link.collection)
            if record["source_key"] == link.source_key
            and record.get("source_record_key") in link.source_record_keys
        ]
        found = {record["source_record_key"] for record in records}
        return records, [key for key in link.source_record_keys if key not in found], False
    id_condition = sql.SQL("AND t.id::text=ANY(%s)") if link.source_record_ids else sql.SQL("")
    params: tuple[Any, ...] = (data.dataset["id"], link.source_key, link.source_record_keys)
    if link.source_record_ids:
        params += (link.source_record_ids,)
    rows = data._fetch(
        sql.SQL(
            "SELECT t.*, t.id::text AS id, t.source_id::text AS source_id, "
            "count(*) OVER() AS total FROM rockygpt_v2.{table} t "
            "JOIN rockygpt_v2.sources s ON s.id=t.source_id "
            "WHERE t.dataset_version_id=%s::uuid AND s.source_key=%s "
            "AND t.source_record_key=ANY(%s) "
            "{id_condition} "
            "AND s.trust_tier IN ('official_primary','official_secondary') "
            "ORDER BY t.id LIMIT 25001"
        ).format(table=sql.Identifier(TABLES[link.collection][0]), id_condition=id_condition),
        params,
    )
    found = {row["source_record_key"] for row in rows}
    missing = [key for key in link.source_record_keys if key not in found]
    if link.source_record_ids:
        found_ids = {str(row["id"]) for row in rows}
        missing.extend(f"id:{key}" for key in link.source_record_ids if key not in found_ids)
    elif link.collection == "events":
        # Legacy sources can contain colliding natural keys. A key alone cannot
        # approve multiple event instances as one identity; publishing pins IDs.
        ambiguous = {key for key, count in Counter(
            row["source_record_key"] for row in rows
        ).items() if count > 1}
        if ambiguous:
            missing.extend(ambiguous)
            rows = [row for row in rows if row["source_record_key"] not in ambiguous]
    records = []
    for row in rows:
        fields = {key: row[key] for key in TABLES[link.collection][1] if row.get(key) is not None}
        record = data._evidence(
            link.collection, row, fields, str(row.get("name") or row.get("title", "")),
            fields.get("event_url") or fields.get("program_url") or fields.get("website_url"),
        )
        if record is None:
            missing.append(row["source_record_key"])
            continue
        record["source_record_key"] = row["source_record_key"]
        record["source_record_id"] = str(row["id"])
        if link.collection == "menu":
            # Keep the same unknown-label semantics as ordinary menu retrieval.
            labels = row.get("label_coverage") or {}
            for label in ("vegan", "vegetarian", "allergens"):
                state = labels.get(label) or ("published" if fields.get(label) else "unknown")
                record["coverage"]["fields"][label] = state
                if state != "published":
                    record["fields"].pop(label, None)
        records.append(record)
    data._enrich(link.collection, records)
    if link.collection == "programs":
        records.extend(catalog_convener_records(data, rows, records))
        records.extend(catalog_program_faculty_records(data, rows))
    elif link.collection == "events":
        records.extend(event_organizer_records(data, rows))
    return records, missing, bool(rows and rows[0]["total"] > len(rows))



def _applicable(
    data: CampusData, records: list[dict[str, Any]], query: ProfileQuery,
) -> list[dict[str, Any]]:
    # Exceptions replace a source's own regular schedule, never another source's
    # conflicting schedule. General hours and meal service also remain separate.
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault((record["collection"], record["source_key"]), []).append(record)
    applicable = []
    for (collection, _), group in groups.items():
        if collection == "events" and query.date is None:
            # Selecting a persistent event chooses its dated occurrence, including a
            # future or historical occurrence. It does not silently become today's event.
            applicable.extend(group)
        elif collection in {"buildings", "schools", "subjects", "graduation_plans",
                            "major_pages"}:
            applicable.extend(group)  # Undated reference records; not search collections.
        else:
            selected = data._dates(group, SearchQuery(
                collection=cast(Collection, collection), date_from=query.date or data.today,
                date_to=query.date or data.today,
            ))
            if collection == "events":
                selected_ids = {record.get("source_record_id") for record in selected}
                selected.extend(record for record in group
                                if record["fields"].get("event_record_id") in selected_ids)
            applicable.extend(selected)
    if query.meal:
        selected = []
        for record in applicable:
            fields = record["fields"]
            if record["collection"] == "menu" and (
                _normalize(str(fields.get("meal", ""))) != _normalize(query.meal)
            ):
                continue
            if record["collection"] == "dining_hours":
                periods = fields.get("periods", [])
                matching = [
                    period for period in periods
                    if _normalize(str(period.get("label", ""))) == _normalize(query.meal)
                ]
                # Retain the full published schedule so a missing meal label
                # cannot silently turn into a claim of closure.
                fields["requested_meal"] = query.meal
                fields["requested_meal_periods"] = matching
                fields["meal_coverage"] = "published" if matching else "not_published"
                if not matching:
                    record["limitations"].append(
                        "The requested meal's hours are not explicitly labeled; "
                        "do not infer meal availability from general opening hours."
                    )
            selected.append(record)
        applicable = selected
    return applicable


def _supports_relationship(record: dict[str, Any], relationship: IdentityRelationship) -> bool:
    def published(field: str) -> bool:
        value: Any = record["fields"]
        for part in field.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return bool(value)

    return any(
        reference.collection == record["collection"]
        and reference.source_key == record["source_key"]
        and reference.source_record_key == record.get("source_record_key")
        and (reference.source_record_id is None
             or reference.source_record_id == record.get("source_record_id"))
        and published(reference.field)
        for reference in relationship.evidence
    )


def _school_names(record: dict[str, Any]) -> set[str]:
    """A school record's current name and its reviewed legacy names."""
    legacy = record["fields"].get("legacy_names") or []
    return {record["fields"].get("name"), *(item.get("name") for item in legacy)} - {None}


def _published_school(record: dict[str, Any]) -> str | None:
    """The school a record publishes; a retired faculty profile names no current school."""
    value = record["fields"].get("school")
    if not isinstance(value, str) or value.endswith(" (Retired)"):
        return None
    return value.removesuffix(" (Adjunct)")


def _room_prefixes(value: Any) -> set[str]:
    """Each room's prefix, or none unless the whole value is PREFIX-NUMBER rooms."""
    if not isinstance(value, str) or not value.strip():
        return set()
    rooms = [ROOM.fullmatch(part.strip()) for part in value.split("/")]
    return {room.group(1) for room in rooms if room} if all(rooms) else set()


def narrows_by_date(matches: list[Identity]) -> bool:
    """Whether a requested date can pick among these matches: several events, no others."""
    return 1 < len(matches) <= 20 and all(entity.kind == "event" for entity in matches)


def _event_date_candidates(
    data: CampusData, matches: list[Identity], query: ProfileQuery,
) -> list[Identity]:
    """A requested date can distinguish instances with the same published title."""
    if query.date is None or not narrows_by_date(matches):
        return matches
    selected = []
    for entity in matches:
        dates: set[CalendarDate] = set()
        uncertain = False
        links = [link for link in entity.links if link.collection == "events"]
        for link in links:
            try:
                records, missing, truncated = _linked_records(data, link)
                uncertain = uncertain or bool(missing or truncated or not records)
                for record in records:
                    if record["fields"].get("event_record_id"):
                        continue
                    day = _date(record["fields"].get("starts_at"))
                    if day is None:
                        uncertain = True
                    else:
                        dates.add(day)
            except Exception:
                uncertain = True
        # An unavailable date cannot disprove that this is the requested event.
        if not links or uncertain or query.date in dates:
            selected.append(entity)
    return selected


def _plan_candidates(
    matches: list[Identity], query: ProfileQuery,
) -> tuple[list[Identity], list[Identity]]:
    """A plan request can pick among same-named programs when only one publishes plans.

    'Computer Science' names the BS, MS, Minor and 4+1, and recommended graduation plans
    are published for the BS alone. The others are returned to be reported, never hidden.
    """
    if len(matches) < 2 or "graduation_plans" not in query.include:
        return matches, []
    publishing = [entity for entity in matches
                  if any(link.collection == "graduation_plans" for link in entity.links)]
    if len(publishing) != 1:
        return matches, []
    return publishing, [entity for entity in matches if entity.id != publishing[0].id]


EVENT_CANDIDATE_LIMIT = 20


def _occurrence_starts(data: CampusData, events: list[Identity]) -> dict[UUID, datetime]:
    """Read linked occurrence start times in one query; unknown starts are absent."""
    owners = {
        row_id: entity.id for entity in events for link in entity.links
        if link.collection == "events" for row_id in link.source_record_ids or []
    }
    if not owners:
        return {}
    rows = data._fetch(
        "SELECT t.id::text AS id, t.starts_at FROM rockygpt_v2.campus_events t "
        "WHERE t.dataset_version_id=%s::uuid AND t.id::text = ANY(%s)",
        (data.dataset["id"], sorted(owners)),
    )
    starts: dict[UUID, datetime] = {}
    for row in rows:
        owner, start = owners.get(str(row.get("id"))), _instant(row.get("starts_at"))
        if owner is not None and start is not None:
            starts[owner] = min(start, starts.get(owner, start))
    return starts


def _chronological(
    events: list[Identity], starts: dict[UUID, datetime], now: datetime,
) -> list[Identity]:
    """Upcoming occurrences soonest first, then past ones latest first, unknown dates last."""
    def key(entity: Identity) -> tuple[int, float, str]:
        start = starts.get(entity.id)
        if start is None:
            return (2, 0.0, str(entity.id))
        stamp = start.timestamp()
        return (0, stamp, str(entity.id)) if start >= now else (1, -stamp, str(entity.id))
    return sorted(events, key=key)


def _on_date(
    events: list[Identity], starts: dict[UUID, datetime], day: CalendarDate | None,
) -> list[Identity]:
    # An unknown start cannot disprove that an occurrence is on the requested date.
    return events if day is None else [
        entity for entity in events
        if entity.id not in starts or starts[entity.id].astimezone(CAMPUS_ZONE).date() == day
    ]


def _group_event_records(
    data: CampusData, group: Identity, registry: IdentityRegistry, query: ProfileQuery,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, dict[str, int]]:
    """Traverse approved incoming organizers, then recheck each event's source proof."""
    linked = [entity for entity in registry.entities if entity.kind == "event" and any(
        relation.type == "organized_by" and relation.target_entity_id == group.id
        for relation in entity.relationships
    )]
    starts = _occurrence_starts(data, linked)
    candidates = _chronological(_on_date(linked, starts, query.date), starts, data.now)
    records: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    missing, failed = 0, 0
    for entity in candidates[:EVENT_CANDIDATE_LIMIT]:
        output = lookup_profile(data, ProfileQuery(
            entity_id=entity.id, include=["event"], date=query.date,
        ))
        component = output["components"].get("event", {})
        supported = [relation for relation in component.get("relationships", [])
                     if relation["type"] == "organized_by"
                     and relation.get("target", {}).get("id") == str(group.id)]
        failed += component.get("failed_links", 0)
        missing += component.get("linked_records_missing", 0)
        if not supported:
            # A dated occurrence outside an explicit filter is not a broken link.
            missing += bool(output["records"] and component.get("relationships_missing"))
            continue
        relationships.extend({**relation, "source": _identity_summary(entity)}
                             for relation in supported)
        for public in output["records"]:
            record = deepcopy(data._seen[public["id"]])
            record["related_to_entity_id"] = str(group.id)
            record["relationship_to_entity"] = "organized_by"
            records.append(record)
    return records, relationships, missing, failed, {
        "linked_event_candidates": len(linked),
        "unexamined_event_candidates": max(0, len(candidates) - EVENT_CANDIDATE_LIMIT),
    }


RELATED_LIMIT = 20
TARGET_KINDS: dict[str, frozenset[str]] = {
    "convener": frozenset({"person"}), "listed_faculty": frozenset({"person"}),
    "organized_by": ARCHWAY_GROUPS,
    "office_at": frozenset({"building"}), "located_at": frozenset({"building"}),
    "part_of": frozenset({"school"}),
}
RELATIONSHIP_MEANINGS: dict[tuple[str, str], str] = {
    ("convener", "outgoing"): "This program's catalog Convener field names the related person.",
    ("convener", "incoming"): "The related program's catalog Convener field names this person.",
    ("listed_faculty", "outgoing"):
        "This program's catalog Program Faculty field lists the related person.",
    ("listed_faculty", "incoming"):
        "The related program's catalog Program Faculty field lists this person.",
    ("profile_course", "outgoing"):
        "This person's undated faculty profile lists the related catalog course.",
    ("organized_by", "outgoing"): "This event's page names the related group as its organizer.",
    ("office_at", "outgoing"):
        "This person's published office room has the related building's room prefix.",
    ("office_at", "incoming"):
        "The related person's published office room has this building's room prefix.",
    ("located_at", "outgoing"): "This office's published room has the related building's room "
                                "prefix.",
    ("located_at", "incoming"): "The related office's published room has this building's room "
                                "prefix.",
    ("part_of", "outgoing"): "Published as part of the related school: a program's catalog school "
                             "(or its reviewed legacy name), or a faculty profile's school.",
    ("part_of", "incoming"): "The related program's catalog school (or its reviewed legacy name), "
                             "or the related person's faculty profile, names this school.",
    ("organized_by", "incoming"): "The related event's page names this group as its organizer.",
    ("includes_course", "outgoing"):
        "The related catalog course's own code starts with this subject's code.",
}
RELATIONSHIP_LIMITATIONS = {
    "convener": "A catalog Convener field is not a verified current appointment.",
    "listed_faculty": "A catalog Program Faculty listing is not a convenership, an appointment "
                      "or a current teaching assignment.",
    "profile_course": "An undated profile course list is not a current teaching assignment.",
    "organized_by": "Only explicitly evidenced organizer links are included; absence does not "
                    "mean a group has no other events.",
    "office_at": "Placed by the published room number's prefix; it says where the office is, "
                 "not the person's school, and covers only published rooms.",
    "located_at": "Placed by the published room number's prefix; it covers only offices with a "
                  "published room, not a complete building directory.",
    "part_of": "Programs still published under the split School of Social Science and Human "
               "Services, and retired faculty, are not placed, so a school's list is not complete.",
    "includes_course": "Catalog courses filed under this subject code; not a schedule of "
                       "current sections, and a program can include courses of other subjects.",
}
# A located_at whose only evidence is a reviewed statement on the building's own record.
REVIEWED_PLACEMENT_MEANINGS = {
    "outgoing": "The related building's campus map record carries a reviewed official "
                "statement that places this office there.",
    "incoming": "This building's campus map record carries a reviewed official statement that "
                "places the related office here.",
}
REVIEWED_PLACEMENT_LIMITATION = (
    "Placed by an official page's statement that a person reviewed, not by a room number; it "
    "gives no room."
)
# A room relationship whose exact published office text a person reviewed as a building.
REVIEWED_READING_MEANINGS = {
    "outgoing": "A person reviewed this entry's published office text as naming the related "
                "building.",
    "incoming": "A person reviewed the related entry's published office text as naming this "
                "building.",
}
REVIEWED_READING_LIMITATION = (
    "Placed by a person's reading of published office text that is not a room number; the text "
    "says no more about the room than it shows."
)
REVIEWED_BASES: dict[str, tuple[dict[str, str], str]] = {
    "reading": (REVIEWED_READING_MEANINGS, REVIEWED_READING_LIMITATION),
    "statement": (REVIEWED_PLACEMENT_MEANINGS, REVIEWED_PLACEMENT_LIMITATION),
}


def _related_records(
    data: CampusData, entity: Identity, registry: IdentityRegistry, query: ProfileQuery,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Follow published relationships in either direction, rechecking each one's evidence.

    Predicate meaning and caveats come from the tables above, so a new relationship type
    needs a table entry rather than a new profile section.
    """
    entities = {item.id: item for item in registry.entities}
    edges: list[tuple[str, Identity, IdentityRelationship]] = []
    if query.direction in {None, "outgoing"}:
        edges += [("outgoing", entity, relation) for relation in entity.relationships
                  if query.relationship in {None, relation.type}]
    if query.direction in {None, "incoming"}:
        edges += [("incoming", source, relation) for source in registry.entities
                  for relation in source.relationships
                  if relation.target_entity_id == entity.id
                  and query.relationship in {None, relation.type}]
    events = [source for direction, source, relation in edges
              if direction == "incoming" and relation.type == "organized_by"]
    starts = _occurrence_starts(data, events)
    if query.date is not None:
        dated = {item.id for item in _on_date(events, starts, query.date)}
        edges = [edge for edge in edges if not (edge[0] == "incoming"
                 and edge[2].type == "organized_by" and edge[1].id not in dated)]
    chronology = _chronological(events, starts, data.now)
    ordered = {item.id: index for index, item in enumerate(chronology)}

    def other(edge: tuple[str, Identity, IdentityRelationship]) -> Identity | None:
        direction, source, relation = edge
        target = relation.target_entity_id
        return source if direction == "incoming" else entities.get(target) if target else None

    def order(edge: tuple[str, Identity, IdentityRelationship]) -> tuple[str, str, int, str]:
        related = other(edge)
        name = related.name if related else str(edge[2].target_record)
        return (edge[2].type, edge[0], ordered.get(edge[1].id, 0), _normalize(name))

    edges.sort(key=order)
    cache: dict[str, tuple[list[dict[str, Any]], list[str], bool]] = {}

    def fetch(link: IdentityLink) -> list[dict[str, Any]]:
        key = link.model_dump_json()
        if key not in cache:
            cache[key] = _linked_records(data, link)
        return cache[key][0]

    records: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    limitations: dict[tuple[str, str], str] = {}
    unverified = failed = 0
    for edge in edges[:RELATED_LIMIT]:
        direction, _, relation = edge
        related = other(edge)
        basis = "published"
        try:
            evidence = [record for reference in relation.evidence for record in fetch(IdentityLink(
                collection=reference.collection, source_key=reference.source_key,
                source_record_keys=[reference.source_record_key],
                source_record_ids=[reference.source_record_id]
                if reference.source_record_id else None,
            )) if _supports_relationship(record, relation)]
            course = relation.target_record
            targets = fetch(IdentityLink(
                collection=course.collection, source_key=course.source_key,
                source_record_keys=[course.source_record_key],
                source_record_ids=[course.source_record_id] if course.source_record_id else None,
            )) if course is not None and direction == "outgoing" else []
            target = entities.get(relation.target_entity_id) if relation.target_entity_id else None
            if relation.type == "part_of" and target is not None:
                # The cited school field must still name this school or a reviewed former name.
                names = {name for link in target.links if link.collection == "schools"
                         for record in fetch(link) for name in _school_names(record)}
                evidence = [record for record in evidence if _published_school(record) in names]
            if relation.type == "includes_course":
                # The cited course's own code must still start with this subject's code.
                codes = {key for link in entity.links if link.collection == "subjects"
                         for key in link.source_record_keys}
                evidence = [record for record in evidence
                            if course_subject(record["fields"].get("code")) in codes]
            if relation.type in ROOM_RELATIONSHIPS and target is not None:
                # Each cited room must still belong to this building by its own prefix, or be
                # an exact published value this building's own record lists as a reviewed
                # reading. A reviewed statement must still be on that record for this office.
                buildings = [record for link in target.links if link.collection == "buildings"
                             for record in fetch(link)]
                prefixes = {prefix for record in buildings
                            for prefix in record["fields"].get("room_prefixes", [])}
                readings = {room for record in buildings
                            for room in record["fields"].get("reviewed_rooms", [])}
                own = {record["id"] for record in buildings}
                bases: dict[str, str] = {}
                for record in evidence:
                    office = record["fields"].get("office")
                    if _room_prefixes(office) & prefixes:
                        bases[record["id"]] = "room"
                    elif isinstance(office, str) and office.strip() in readings:
                        bases[record["id"]] = "reading"
                    elif record["id"] in own and any(
                            isinstance(item, dict) and item.get("entity_id") == str(edge[1].id)
                            for item in record["fields"].get("reviewed_locations", [])):
                        bases[record["id"]] = "statement"
                evidence = [record for record in evidence if record["id"] in bases]
                basis = next((kind for kind in ("room", "reading", "statement")
                              if kind in bases.values()), basis)
                if direction == "outgoing":
                    targets = buildings  # The building's own map record names it.
        except Exception:
            # One broken link must not erase the other relationships.
            failed += 1
            continue
        allowed = TARGET_KINDS.get(relation.type)
        organizers = {(record["fields"].get("organizer_group_id"),
                       record["fields"].get("organizer_url"),
                       _normalize(str(record["fields"].get("organizer_name", ""))))
                      for record in evidence}
        if (not evidence or (allowed is not None and (target is None or target.kind not in allowed))
                or (relation.type == "organized_by" and len(organizers) != 1)
                or (course is not None and direction == "outgoing" and not targets)):
            unverified += 1  # Retained in the registry, but not re-established here.
            continue
        if basis in REVIEWED_BASES:
            meanings, limitation = REVIEWED_BASES[basis]
            meaning = meanings[direction]
        else:
            meaning = RELATIONSHIP_MEANINGS[(relation.type, direction)]
            limitation = RELATIONSHIP_LIMITATIONS[relation.type]
        limitations[(relation.type, basis)] = limitation
        summary: dict[str, Any] = {
            "type": relation.type, "direction": direction, "meaning": meaning,
            "evidence_ids": [record["id"] for record in evidence],
        }
        if related is not None:
            summary["entity"] = _identity_summary(related)
        if course is not None:
            summary["target_record"] = course.model_dump()
            summary["target_evidence_ids"] = [record["id"] for record in targets]
        elif targets:
            summary["target_evidence_ids"] = [record["id"] for record in targets]
        summaries.append(summary)
        # A subject's course, or a statement's building, is both the evidence and the target.
        for record in [*evidence, *targets]:
            if any(kept["id"] == record["id"] for kept in records):
                continue
            copied = deepcopy(record)
            copied["limitations"].append(limitation)
            records.append(copied)
    return records, summaries, {
        "relationship_filter": query.relationship, "direction_filter": query.direction,
        "relationship_candidates": len(edges),
        "unexamined_relationship_candidates": max(0, len(edges) - RELATED_LIMIT),
        "unverified_relationships": unverified, "failed_relationships": failed,
        "limitations": [limitations[key] for key in sorted(limitations)],
    }


REQUIREMENT_LIMITATION = (
    "Catalog requirement structure: a course in a choose-N or either/or group is an option, "
    "not a required course on its own."
)
UNINTERPRETED_LIMITATION = (
    "Some conditions are shown as published without interpretation; read condition, count "
    "and credits together."
)
UNLINKED_LIMITATION = "Some cited codes are not catalog courses; they are shown as published."
PLAN_LIMITATION = (
    "Recommended graduation plans are suggested course sequences, one set per admission cohort. "
    "A plan applies only to students admitted in its cohort; it is not a degree requirement."
)
# What a plan summary keeps: that the plan exists, for whom, and its stated totals.
PLAN_SUMMARY_FIELDS = ("name", "cohort", "variantOf", "applicability", "totalCredits",
                       "graduateCredits", "gpa", "totals", "url")
PLAN_SUMMARY_LIMITATION = (
    "Plan summary: it establishes that this plan is published, for its cohort, with its "
    "stated totals. Its semesters are not included; request the plan by name to read them."
)
SEASONS = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}


def _cohort_order(label: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(winter|spring|summer|fall) (\d{4})", _normalize(label))
    return (int(match[2]), SEASONS[match[1]]) if match else None


def _plan_cohort(
    data: CampusData, records: list[dict[str, Any]], requested: str | None,
    plan: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One cohort's plans, never a mix: the requested cohort, or else the newest one.

    The program's own plan arrives whole and each variant as a summary, unless a plan is
    named. When the requested cohort or plan is not published, summaries of the published
    ones are the evidence for saying so. Summaries are marked `_plan_summary`.
    """
    labels = {str(r["fields"]["cohort"]) for r in records if r["fields"].get("cohort")}
    ordered = sorted(labels, key=lambda label: (_cohort_order(label) or (0, -1), label),
                     reverse=True)
    if requested is None:
        selected = next((label for label in ordered if _cohort_order(label)), None)
    else:
        selected = next((label for label in ordered
                         if _normalize(label) == _normalize(requested)), None)
    scope: dict[str, Any] = {
        "cohort": selected,
        "cohort_selection": "newest_published" if requested is None else "requested",
        "available_cohorts": ordered,
    }
    if not ordered:
        return [], scope
    # The official index's order, which lists a program's plan before its variants.
    plans = (data._artifact("graduation-plans") or {}).get("plans") or []
    position = {str(plan.get("id")): index for index, plan in enumerate(plans)
                if isinstance(plan, dict)}

    def cohort_plans(label: str) -> list[dict[str, Any]]:
        return sorted((r for r in records if r["fields"].get("cohort") == label),
                      key=lambda r: position.get(str(r.get("source_record_key")), len(position)))

    def summarized(chosen: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for record in chosen:
            record["_plan_summary"] = True
        return chosen

    if selected is None:
        scope["reason"] = "cohort_not_published" if requested else "cohort_required"
        return summarized([r for label in ordered for r in cohort_plans(label)
                           if not r["fields"].get("variantOf")]), scope
    chosen = cohort_plans(selected)
    scope["available_plans"] = [str(r["fields"].get("name")) for r in chosen]
    if plan is not None:
        # This program's own published plan names only: an exact name, or else the one
        # name containing every requested word ('Data Science 4+1'), never a guess.
        def words(text: str) -> set[str]:
            return set(re.findall(r"[\w+]+", _normalize(text)))

        def name(record: dict[str, Any]) -> str:
            return str(record["fields"].get("name", ""))

        named = [r for r in chosen if _normalize(name(r)) == _normalize(plan)] or [
            r for r in chosen if words(plan) <= words(name(r))]
        if len(named) != 1:
            scope["reason"] = "plan_ambiguous" if named else "plan_not_published"
            return summarized(named or chosen), scope
        return named, scope
    for record in chosen:
        if record["fields"].get("variantOf"):
            record["_plan_summary"] = True
    return chosen, scope


def _course_text(course: dict[str, Any]) -> str:
    return " ".join(str(part) for part in (course.get("code"), course.get("name")) if part)


def _render_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Compact, faithful view: published condition/count/credits, derived choice, options."""
    rendered = {key: rule[key] for key in (
        "condition", "count", "credits", "choose", "name", "note", "text", "constraints")
                if rule.get(key) is not None}
    if rule.get("items"):
        rendered["options"] = [
            f" {item.get('logic') or 'and'} ".join(
                _course_text(course) for course in item.get("courses", []))
            for item in rule["items"]
        ]
    if rule.get("sub_rules"):
        rendered["parts"] = [_render_rule(sub) for sub in rule["sub_rules"]]
    return rendered


def _uninterpreted(rule: dict[str, Any]) -> bool:
    return rule.get("choose") is None or any(
        _uninterpreted(sub) for sub in rule.get("sub_rules", []))


def _unlinked(rule: dict[str, Any] | None, listed: dict[str, Any] | None) -> bool:
    def courses(node: dict[str, Any]) -> list[dict[str, Any]]:
        own = [course for item in node.get("items", []) for course in item.get("courses", [])]
        return own + [course for sub in node.get("sub_rules", []) for course in courses(sub)]
    cited = (courses(rule) if rule else []) + (listed.get("courses", []) if listed else [])
    return any(course.get("course_id") is None for course in cited)


def _requirement_records(
    data: CampusData, entity: Identity,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A program's published requirement groups, in catalog section order."""
    coverage: dict[str, Any] = {"requirement_groups": 0, "shared_requirement_groups": 0,
                                "missing_requirement_groups": 0}
    if entity.kind != "program":
        return [], {**coverage, "reason": "requirements_apply_to_programs"}
    artifact = data._artifact("program-requirement-groups")
    if not isinstance(artifact, dict) or not isinstance(artifact.get("groups"), list) \
            or not isinstance(artifact.get("edges"), list):
        return [], {**coverage, "reason": "requirement_groups_not_published"}
    source = next((item for item in data.sources.values()
                   if item["source_key"] == "academic-programs"), None)
    if source is None:
        return [], {**coverage, "reason": "catalog_source_unavailable"}
    groups = {group.get("id"): group for group in artifact["groups"] if isinstance(group, dict)}
    links = sorted((edge for edge in artifact["edges"] if isinstance(edge, dict)
                    and edge.get("type") == "requirement_group"
                    and edge.get("source", {}).get("entity_id") == str(entity.id)),
                   key=lambda edge: edge.get("order", 0))
    programs = data._artifact("programs") or {}
    records = []
    for link in links:
        group = groups.get(link.get("target", {}).get("record_id"))
        if group is None:
            coverage["missing_requirement_groups"] += 1
            continue
        rule, listed = group.get("rule"), group.get("course_list")
        requirement = _render_rule(rule) if rule else {
            key: value for key, value in {
                "select_count": listed.get("select_count"), "choose": listed.get("choose"),
                "options": [_course_text(course) for course in listed.get("courses", [])],
            }.items() if value is not None
        } if listed else {}
        fields = {
            "program": entity.name, "section": group.get("label"),
            "section_order": link.get("order"), "shape": group.get("shape"),
            **({"note": group["note"]} if group.get("note") else {}),
            "requirement": requirement,
            "shared_by_program_sections": group.get("program_sections"),
            "requirement_group_id": group.get("id"),
        }
        record = data._evidence("program_requirements", {
            # One record per program, even for a shared group, so program fields never merge.
            "id": f"{group['id']}@{entity.id}", "source_id": source["id"],
            "source_record_key": group["id"], "collected_at": source.get("completed_at"),
        }, fields, f"{entity.name} — {group.get('label')}", _catalog_url(programs, link))
        if record is None:
            coverage["missing_requirement_groups"] += 1
            continue
        record["related_to_entity_id"] = str(entity.id)
        record["relationship_to_entity"] = "requirement_group"
        record["limitations"].append(REQUIREMENT_LIMITATION)
        if rule and _uninterpreted(rule):
            record["limitations"].append(UNINTERPRETED_LIMITATION)
        if _unlinked(rule, listed):
            record["limitations"].append(UNLINKED_LIMITATION)
        records.append(record)
    coverage["requirement_groups"] = len(records)
    coverage["shared_requirement_groups"] = sum(
        1 for record in records if (record["fields"].get("shared_by_program_sections") or 0) > 1)
    return records, coverage


def _catalog_url(programs: Any, link: dict[str, Any]) -> str | None:
    """The program's catalog page, found by the link's exact path, never by name."""
    path = link.get("path")
    if not isinstance(path, list) or len(path) != 6 or path[0::2] != [
            "schools", "majors", "requirements"] or not all(
            type(index) is int and index >= 0 for index in path[1::2]):
        return None
    try:
        major = programs["schools"][path[1]]["majors"][path[3]]
    except (KeyError, IndexError, TypeError):
        return None
    url = major.get("catalogUrl") or major.get("url") if isinstance(major, dict) else None
    return url if isinstance(url, str) else None


def lookup_profile(data: CampusData, query: ProfileQuery) -> dict[str, Any]:
    """Identity links establish identity only, never authority or a missing attribute."""
    data._ensure_loaded()
    result: dict[str, Any] = {
        "status": "ok",
        "dataset_version": data.dataset["version"],
        "records": [],
        "total_matches": 0,
        "truncated": False,
        "resolution": {"status": "unavailable", "entity": None, "candidates": []},
        "components": {},
        "coverage": {"scope": "curated_identity_links_only", "absence_is_not_nonexistence": True},
    }
    try:
        payload = data._artifact("campus-identities")
        if payload is None:
            return {**result, "status": "unavailable", "reason": "identity_registry_unavailable"}
        registry = IdentityRegistry.model_validate(payload)
    except ValidationError:
        return {**result, "status": "unavailable", "reason": "invalid_identity_registry"}
    normalized = _normalize(query.entity) if query.entity is not None else None
    matches = [
        entity
        for entity in registry.entities
        if (query.entity_id is not None and entity.id == query.entity_id)
        or (
            normalized is not None
            and normalized in lookup_terms(entity)
        )
    ]
    read_as = (_without_campus_prefix(query.entity)
               if query.entity is not None and not matches else None)
    if read_as is not None:
        # Only after the name as given matches nothing, and the rest must still be an
        # exact name or alias.
        result["resolution"]["read_as"] = read_as
        matches = [entity for entity in registry.entities
                   if _normalize(read_as) in lookup_terms(entity)]
    matches = _event_date_candidates(data, matches, query)
    matches, set_aside = _plan_candidates(matches, query)
    if len(matches) != 1:
        result["resolution"].update(
            status="ambiguous" if matches else "no_match",
            candidates=[_identity_summary(entity) for entity in matches[:20]],
            total_candidates=len(matches),
            candidates_truncated=len(matches) > 20,
        )
        return result

    entity = matches[0]
    result["resolution"].update(status="matched", entity=_identity_summary(entity))
    if set_aside:
        # Name the program the plans belong to; the same name also means these.
        result["resolution"].update(
            narrowed_by="graduation_plans",
            other_candidates=[_identity_summary(other) for other in set_aside[:20]],
        )
    entities = {item.id: item for item in registry.entities}
    cache: dict[str, tuple[list[dict[str, Any]], list[str], bool]] = {}
    matched_record_ids: set[str] = set()
    for component in query.include:
        links = [link for link in entity.links if link.collection in SECTION_COLLECTIONS[component]]
        records: list[dict[str, Any]] = []
        missing_keys: list[str] = []
        failed_links = 0
        truncated = False
        for link in links:
            try:
                cache_key = link.model_dump_json()
                if cache_key not in cache:
                    cache[cache_key] = _linked_records(data, link)
                fetched, missing, cut = cache[cache_key]
                missing_keys.extend(missing)
                truncated = truncated or cut
                for original in fetched:
                    if original.get("_relationship_evidence_only"):
                        continue  # Rechecked by the related section, never section content.
                    record = deepcopy(original)
                    # Keep already hydrated identity fields for the read model's
                    # derivation metadata without broadening requested facts.
                    record["_entity_lineage_fields"] = {
                        key: original["fields"][key] for key in ("name", "school", "email")
                        if key in original["fields"]
                    }
                    if record["collection"] == "menu":
                        record["related_to_entity_id"] = str(entity.id)
                        record["relationship_to_entity"] = "offering_at"
                    else:
                        record["canonical_entity_id"] = str(entity.id)
                    if component == "contact" and record["collection"] == "faculty":
                        _select_fields(record, ("name", "title", "email", "phone", "office"))
                    elif component == "contact" and record["collection"] == "clubs":
                        _select_fields(record, (
                            "name", "email", "website_url", "instagramUrl",
                            "groupmeUrls", "groupmeGroups",
                        ))
                    elif component == "courses":
                        _select_fields(record, ("name", "courses"))
                    if component == "hours":
                        record["fields"]["availability_scope"] = (
                            "dining_service" if record["collection"] == "dining_hours"
                            else "unspecified"
                        )
                        record["limitations"].append(
                            "Published operating schedule; it does not establish staff or "
                            "telephone availability; never infer phone-answering hours."
                        )
                    records.append(record)
            except Exception:
                # A broken link must not erase independently available sections.
                failed_links += 1
        records = _applicable(data, records, query)
        if component == "menu" and query.diet is not None:
            # A published label only; an unknown label is dropped, never treated as false.
            records = [record for record in records if record["fields"].get(query.diet) is True]
        plan_scope: dict[str, Any] = {}
        if component == "graduation_plans":
            records, plan_scope = _plan_cohort(data, records, query.cohort, query.plan)
            for record in records:
                if record.get("_plan_summary"):
                    _select_fields(record, PLAN_SUMMARY_FIELDS)
                    record["limitations"].append(PLAN_SUMMARY_LIMITATION)
        relationships: list[dict[str, Any]] = []
        relationship_missing = 0
        reverse_coverage: dict[str, int] = {}
        related_coverage: dict[str, Any] = {}
        if component == "related":
            related_records, relationships, related_coverage = _related_records(
                data, entity, registry, query,
            )
            records.extend(related_records)
            relationship_missing += related_coverage["unverified_relationships"]
            failed_links += related_coverage["failed_relationships"]
            truncated = truncated or bool(related_coverage["unexamined_relationship_candidates"])
        placement: list[dict[str, Any]] = []
        if component == "contact":
            # Where to find the entity: its own building placements, rechecked as in the
            # related section and cited with each building's map record.
            for kind in sorted(ROOM_RELATIONSHIPS & {item.type for item in entity.relationships}):
                placed, summaries, _ = _related_records(data, entity, registry, query.model_copy(
                    update={"relationship": kind, "direction": "outgoing"}))
                held = {record["id"] for record in records}
                records.extend(record for record in placed if record["id"] not in held)
                placement.extend(summaries)
        requirement_coverage: dict[str, Any] = {}
        if component == "requirements":
            requirement_records, requirement_coverage = _requirement_records(data, entity)
            records.extend(requirement_records)
            relationship_missing += requirement_coverage["missing_requirement_groups"]
        if component == "event" and entity.kind in ARCHWAY_GROUPS:
            related, relationships, reverse_missing, reverse_failed, reverse_coverage = (
                _group_event_records(data, entity, registry, query)
            )
            records.extend(related)
            relationship_missing += reverse_missing
            failed_links += reverse_failed
            truncated = truncated or bool(reverse_coverage["unexamined_event_candidates"])
        if component in {"conveners", "courses", "program", "event"}:
            for relationship in entity.relationships:
                expected = ("profile_course" if component == "courses" else "organized_by"
                            if component == "event" else "convener")
                if relationship.type != expected:
                    continue
                evidence = [r for r in records if _supports_relationship(r, relationship)]
                if not evidence:
                    relationship_missing += 1
                    continue
                if relationship.type == "organized_by" and len({
                    (record["fields"].get("organizer_group_id"),
                     record["fields"].get("organizer_url"),
                     _normalize(str(record["fields"].get("organizer_name", ""))))
                    for record in evidence
                }) != 1:
                    relationship_missing += 1
                    continue  # Retain conflicting captures without choosing an organizer.
                summary: dict[str, Any] = {
                    "type": relationship.type,
                    "evidence_ids": [r["id"] for r in evidence],
                }
                if relationship.type in {"convener", "organized_by"}:
                    assert relationship.target_entity_id is not None
                    target = entities.get(relationship.target_entity_id)
                    if target is None or target.kind not in TARGET_KINDS[relationship.type]:
                        relationship_missing += 1
                        continue
                    summary["target"] = _identity_summary(target)
                else:
                    reference = relationship.target_record
                    assert reference is not None
                    try:
                        related, missing, cut = _linked_records(data, IdentityLink(
                            collection=reference.collection, source_key=reference.source_key,
                            source_record_keys=[reference.source_record_key],
                            source_record_ids=[reference.source_record_id]
                            if reference.source_record_id else None,
                        ))
                        relationship_missing += len(missing)
                        truncated = truncated or cut
                        for record in related:
                            record["related_to_entity_id"] = str(entity.id)
                            record["limitations"].append(
                                "Linked from an undated faculty-profile course list; "
                                "this is not a current teaching assignment."
                            )
                        records.extend(related)
                        summary["target_record"] = reference.model_dump()
                        summary["target_evidence_ids"] = [r["id"] for r in related]
                    except Exception:
                        failed_links += 1
                        continue
                relationships.append(summary)
                for record in evidence:
                    record["fields"].setdefault("identity_relationships", []).append(summary)
        records = list({record["id"]: record for record in records}.values())
        matched_record_ids.update(record["id"] for record in records)
        total_matches = len(records)
        if component == "menu":
            # A normal profile request needs a bounded, cited selection, as an
            # ordinary search does. Stable published station/name ordering adds
            # no inferred food category or ranking and keeps every record whole.
            records.sort(key=lambda record: (
                _normalize(str(record["fields"].get("station", ""))),
                _normalize(str(record["fields"].get("name", ""))), record["id"],
            ))
            records = records[:query.menu_limit]
            truncated = truncated or len(records) < total_matches
        if component == "contact":
            field_names = tuple(field for field in TABLES["contacts"][1] if field != "name")
            if any(record["collection"] == "clubs" for record in records):
                field_names += ("website_url", "instagramUrl", "groupmeUrls")
        elif component == "hours":
            field_names = ("hours", "schedule", "periods")
        elif component == "faculty":
            field_names = ("email", "phone", "office", "title", "courses")
        elif component == "club":
            field_names = ("category", "bucket", "email", "website_url", "instagramUrl",
                           "groupmeUrls", "mission", "memberBenefits", "membershipInfo")
        elif component == "building":
            field_names = ("category", "room_prefixes", "map_url")
        elif component == "school":
            field_names = ("abbreviation", "url", "legacy_names")
        elif component == "subject":
            field_names = ("code", "name", "course_count")
        elif component == "event" and entity.kind not in ARCHWAY_GROUPS:
            field_names = ("date_label", "start_time", "end_time", "organizer", "location",
                           "location_access", "event_url", "organizer_group_id", "organizer_url",
                           "organizer_name")
        else:
            field_names = ()
        coverage, conflicts = _field_coverage(records, field_names)
        # Each record's conflicts, by label, and the record field each label is about.
        disagreeing = {record["id"]: {key: key for key in conflicts} for record in records}
        # Each published schedule is its own scope: dining service and general campus hours,
        # and the named schedules one source lists apart, such as a building and its help
        # desk. Only records of the same schedule can disagree. Records from different
        # sources, or without a name, are always compared: a different name there doesn't
        # show a different schedule.
        scopes: dict[tuple[str, str], list[dict[str, Any]]] = {}
        collections: dict[str, list[dict[str, Any]]] = {}
        if component == "hours":
            for record in records:
                collections.setdefault(record["collection"], []).append(record)
        for collection, members in collections.items():
            titles = [_normalize(str(record["title"])) for record in members]
            apart = len({record["source_key"] for record in members}) == 1 and all(titles)
            for record, title in zip(members, titles, strict=True):
                scopes.setdefault((collection, title if apart else ""), []).append(record)
        if len(scopes) > 1:
            named = Counter(collection for collection, _ in scopes)
            conflicts, disagreeing = {}, {}
            for (collection, _), members in scopes.items():
                _, scoped = _field_coverage(members, field_names)
                title = members[0]["title"]
                prefix = f"{collection}.{title}" if named[collection] > 1 else collection
                for field, values in scoped.items():
                    conflicts[f"{prefix}.{field}"] = values
                    for record in members:
                        disagreeing.setdefault(record["id"], {})[f"{prefix}.{field}"] = field
            coverage = {key: "not_published" if state == "not_published" else "published"
                        for key, state in coverage.items()}
            coverage.update({key: "conflict" for key in conflicts})
        for record in records:
            own = disagreeing.get(record["id"], {})
            # A related building's record is not one of the entity's disagreeing records.
            if own and record.get("canonical_entity_id") == str(entity.id):
                record["coverage"]["fields"].update({field: "conflict" for field in own.values()})
                record["limitations"].append(
                    "Linked records disagree on " + ", ".join(own)
                    + "; identity does not establish which value is authoritative."
                )
            record["content"] = _json(record["fields"])
            previous = data._seen.get(record["id"])
            if previous:
                stored = deepcopy(record)
                stored["fields"] = {**previous["fields"], **record["fields"]}
                stored["content"] = _json(stored["fields"])
                stored["limitations"] = list(dict.fromkeys(
                    previous["limitations"] + record["limitations"]
                ))
                stored["coverage"]["fields"].update(previous["coverage"]["fields"])
                data._seen[record["id"]] = stored
            else:
                data._seen[record["id"]] = record
        result["components"][component] = {
            "status": (
                "partial" if records and (
                    failed_links or missing_keys or truncated or relationship_missing
                ) else "available" if records
                else "unavailable" if failed_links else "missing"
            ),
            "evidence_ids": [record["id"] for record in records],
            "fields": coverage,
            "conflicts": conflicts,
            "linked_records_missing": len(set(missing_keys)),
            "relationships_missing": relationship_missing,
            "failed_links": failed_links,
            "truncated": truncated,
            "total_matches": total_matches,
            "returned_count": len(records),
            "omitted_count": total_matches - len(records),
        }
        if component == "menu" and len(records) < total_matches:
            result["components"][component]["reason"] = "menu_item_limit"
        if component in {"conveners", "courses", "program", "event"}:
            result["components"][component]["relationships"] = relationships
            if component == "conveners" and not relationships:
                result["components"][component]["status"] = "missing"
                result["components"][component]["reason"] = "no_supported_person_identity_link"
        if component in {"hours", "menu"}:
            result["components"][component].update(
                service_date=(query.date or data.today).isoformat(),
                meal=query.meal,
            )
        if component == "menu" and query.diet is not None:
            result["components"][component].update(
                diet=query.diet,
                limitations=[f"Only items the menu labels {query.diet}; an item with no "
                             "published label is left out, not established as excluded."],
            )
        if component == "hours":
            result["components"][component]["availability_scope"] = (
                "dining_service" if records and all(
                    r["collection"] == "dining_hours" for r in records
                ) else "unspecified"
            )
        if component == "courses":
            result["components"][component]["temporal_scope"] = "undated_profile_list"
        if component == "related":
            result["components"][component].update(relationships=relationships, **related_coverage)
        if component == "contact":
            result["components"][component]["placement"] = placement
        if component == "requirements":
            result["components"][component].update(
                **requirement_coverage, limitations=[REQUIREMENT_LIMITATION])
        if component == "graduation_plans":
            result["components"][component].update(**plan_scope, limitations=[PLAN_LIMITATION])
            if plan_scope.get("reason") in {
                    "cohort_not_published", "plan_not_published", "plan_ambiguous"}:
                # The summaries show what is published; the requested plan is still missing.
                result["components"][component]["status"] = "missing"
        if component == "club":
            result["components"][component]["limitations"] = [
                "A directory listing does not establish current meetings, membership, or events."
            ]
        if component == "event":
            result["components"][component].update(
                temporal_scope="linked_event_occurrences" if entity.kind in ARCHWAY_GROUPS
                else "dated_event_occurrence", timezone="America/New_York",
                requested_date=query.date.isoformat() if query.date else None,
                occurrence_dates=sorted({record["fields"]["occurrence_date"] for record in records
                                         if record["fields"].get("occurrence_date")}),
                **reverse_coverage,
            )
            if entity.kind in ARCHWAY_GROUPS:
                result["components"][component]["limitations"] = [
                    "Only events with an approved, evidenced organizer link are included; "
                    "absence does not mean this organization has no events."
                ]
                if reverse_coverage["unexamined_event_candidates"]:
                    result["components"][component]["reason"] = "event_candidate_limit"
        # A plan excerpt would drop semesters, so a delivered plan arrives whole.
        result["records"].extend(
            data._public(record, detail=component == "graduation_plans"
                         and not record.get("_plan_summary")) for record in records)
        result["truncated"] = result["truncated"] or truncated
    # A section may reuse evidence from another section; retain its richest version.
    merged: dict[str, dict[str, Any]] = {}
    for record in result["records"]:
        if record["id"] in merged:
            previous = merged[record["id"]]
            record["fields"] = {**previous["fields"], **record["fields"]}
            record["limitations"] = list(dict.fromkeys(
                previous["limitations"] + record["limitations"]
            ))
            record["coverage"]["fields"].update(previous["coverage"]["fields"])
        merged[record["id"]] = record
    result["records"] = list(merged.values())
    result["total_matches"] = len(matched_record_ids)
    if not result["records"] and any(
        component["status"] == "unavailable" for component in result["components"].values()
    ):
        result["status"] = "unavailable"
    from rockygpt_brain.retrieval.entity_evidence import profile_facts

    result["entity_facts"] = profile_facts(data, result)
    return result
