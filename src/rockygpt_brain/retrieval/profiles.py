"""Resolve curated identities and retrieve their independently sourced records."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date as CalendarDate
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

from rockygpt_brain.retrieval.helpers import _json
from rockygpt_brain.retrieval.models import TABLES, Collection, SearchQuery
from rockygpt_brain.retrieval.processing import catalog_convener_records

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

ProfileSection = Literal["contact", "hours", "faculty", "courses", "program", "conveners", "menu"]
LinkCollection = Literal[
    "contacts", "campus_hours", "dining_hours", "menu", "faculty", "programs", "courses"
]
SECTION_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "contact": ("contacts", "faculty"),
    "hours": ("campus_hours", "dining_hours"),
    "faculty": ("faculty",),
    "courses": ("faculty",),
    "program": ("programs",),
    "conveners": ("programs",),
    "menu": ("menu",),
}


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


class ProfileQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str | None = Field(default=None, min_length=1, max_length=240)
    entity_id: UUID | None = None
    include: list[ProfileSection] = Field(min_length=1, max_length=7)
    date: CalendarDate | None = Field(
        default=None, description="Campus-local service date; null means the current campus date."
    )
    meal: str | None = Field(
        default=None, min_length=1, max_length=80,
        description="Published meal label for menu and dining periods, or null for all meals.",
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
        return self


class IdentityLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection: LinkCollection
    source_key: RecordKey
    source_record_keys: list[RecordKey] = Field(min_length=1, max_length=25000)


class RecordReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection: LinkCollection
    source_key: RecordKey
    source_record_key: RecordKey


class RelationshipEvidence(RecordReference):
    field: RecordKey
    source_url: RecordKey | None = None


class IdentityRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["convener", "profile_course"]
    target_entity_id: UUID | None = None
    target_record: RecordReference | None = None
    evidence: list[RelationshipEvidence] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def target_matches_type(self) -> IdentityRelationship:
        if self.type == "convener":
            if self.target_entity_id is None or self.target_record is not None:
                raise ValueError("A convener relationship targets a person identity")
        elif self.target_record is None or self.target_entity_id is not None:
            raise ValueError("A profile_course relationship targets a catalog record")
        elif self.target_record.collection != "courses":
            raise ValueError("A profile_course target must be a catalog course")
        return self


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal["office", "person", "facility", "venue", "program"]
    name: IdentityText
    aliases: list[IdentityText] = Field(max_length=32)
    links: list[IdentityLink] = Field(min_length=1, max_length=32)
    relationships: list[IdentityRelationship] = Field(default_factory=list, max_length=1000)

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
        owners: dict[tuple[str, str, str], UUID] = {}
        for entity in self.entities:
            for link in entity.links:
                for key in link.source_record_keys:
                    reference = (link.collection, link.source_key, key)
                    if reference in owners:
                        raise ValueError("Source record links must not be duplicated")
                    owners[reference] = entity.id
        return self


def _identity_summary(entity: Identity) -> dict[str, str]:
    return {"id": str(entity.id), "name": entity.name, "kind": entity.kind}


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
    if link.collection in {"faculty", "courses"}:
        records = [
            deepcopy(record) for record in data._load(link.collection)
            if record["source_key"] == link.source_key
            and record.get("source_record_key") in link.source_record_keys
        ]
        found = {record["source_record_key"] for record in records}
        return records, [key for key in link.source_record_keys if key not in found], False
    rows = data._fetch(
        sql.SQL(
            "SELECT t.*, t.id::text AS id, t.source_id::text AS source_id, "
            "count(*) OVER() AS total FROM rockygpt_v2.{table} t "
            "JOIN rockygpt_v2.sources s ON s.id=t.source_id "
            "WHERE t.dataset_version_id=%s::uuid AND s.source_key=%s "
            "AND t.source_record_key=ANY(%s) "
            "AND s.trust_tier IN ('official_primary','official_secondary') "
            "ORDER BY t.id LIMIT 25001"
        ).format(table=sql.Identifier(TABLES[link.collection][0])),
        (data.dataset["id"], link.source_key, link.source_record_keys),
    )
    found = {row["source_record_key"] for row in rows}
    missing = [key for key in link.source_record_keys if key not in found]
    records = []
    for row in rows:
        fields = {key: row[key] for key in TABLES[link.collection][1] if row.get(key) is not None}
        record = data._evidence(
            link.collection, row, fields, str(row["name"]), fields.get("program_url")
        )
        if record is None:
            missing.append(row["source_record_key"])
            continue
        record["source_record_key"] = row["source_record_key"]
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
    return records, missing, bool(rows and rows[0]["total"] > len(rows))



def _applicable(
    data: CampusData, records: list[dict[str, Any]], query: ProfileQuery,
) -> list[dict[str, Any]]:
    # Exceptions replace a source's own regular schedule, never another source's
    # conflicting schedule. General hours and meal service also remain separate.
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault((record["collection"], record["source_key"]), []).append(record)
    applicable = [
        record
        for (collection, _), group in groups.items()
        for record in data._dates(group, SearchQuery(
            collection=cast(Collection, collection), date_from=query.date or data.today,
            date_to=query.date or data.today,
        ))
    ]
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
        and published(reference.field)
        for reference in relationship.evidence
    )


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
            and normalized in {_normalize(value) for value in [entity.name, *entity.aliases]}
        )
    ]
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
    entities = {item.id: item for item in registry.entities}
    cache: dict[str, tuple[list[dict[str, Any]], list[str], bool]] = {}
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
                    record = deepcopy(original)
                    if record["collection"] == "menu":
                        record["related_to_entity_id"] = str(entity.id)
                        record["relationship_to_entity"] = "offering_at"
                    else:
                        record["canonical_entity_id"] = str(entity.id)
                    if component == "contact" and record["collection"] == "faculty":
                        _select_fields(record, ("name", "title", "email", "phone", "office"))
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
        relationships: list[dict[str, Any]] = []
        relationship_missing = 0
        if component in {"conveners", "courses", "program"}:
            for relationship in entity.relationships:
                expected = "profile_course" if component == "courses" else "convener"
                if relationship.type != expected:
                    continue
                evidence = [r for r in records if _supports_relationship(r, relationship)]
                if not evidence:
                    relationship_missing += 1
                    continue
                summary: dict[str, Any] = {
                    "type": relationship.type,
                    "evidence_ids": [r["id"] for r in evidence],
                }
                if relationship.type == "convener":
                    assert relationship.target_entity_id is not None
                    target = entities.get(relationship.target_entity_id)
                    if target is None or target.kind != "person":
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
        if component == "contact":
            field_names = tuple(field for field in TABLES["contacts"][1] if field != "name")
        elif component == "hours":
            field_names = ("hours", "schedule", "periods")
        elif component == "faculty":
            field_names = ("email", "phone", "office", "title", "courses")
        else:
            field_names = ()
        coverage, conflicts = _field_coverage(records, field_names)
        # Dining service and general campus hours are distinct scopes.
        if component == "hours" and len({r["collection"] for r in records}) > 1:
            conflicts = {}
            for collection in ("campus_hours", "dining_hours"):
                _, scoped = _field_coverage(
                    [r for r in records if r["collection"] == collection], field_names
                )
                conflicts.update({f"{collection}.{key}": value for key, value in scoped.items()})
            coverage = {key: "published" for key in coverage if coverage[key] != "not_published"}
            coverage.update({key: "conflict" for key in conflicts})
        for record in records:
            if conflicts:
                record["coverage"]["fields"].update({field: "conflict" for field in conflicts})
                record["limitations"].append(
                    "Linked records disagree on " + ", ".join(conflicts)
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
        }
        if component in {"conveners", "courses", "program"}:
            result["components"][component]["relationships"] = relationships
            if component == "conveners" and not relationships:
                result["components"][component]["status"] = "missing"
                result["components"][component]["reason"] = "no_supported_person_identity_link"
        if component in {"hours", "menu"}:
            result["components"][component].update(
                service_date=(query.date or data.today).isoformat(),
                meal=query.meal,
            )
        if component == "hours":
            result["components"][component]["availability_scope"] = (
                "dining_service" if records and all(
                    r["collection"] == "dining_hours" for r in records
                ) else "unspecified"
            )
        if component == "courses":
            result["components"][component]["temporal_scope"] = "undated_profile_list"
        result["records"].extend(data._public(record) for record in records)
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
    result["total_matches"] = len(result["records"])
    if not result["records"] and any(
        component["status"] == "unavailable" for component in result["components"].values()
    ):
        result["status"] = "unavailable"
    return result
