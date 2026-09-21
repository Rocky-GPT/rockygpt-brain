"""Resolve curated identities and retrieve their independently sourced records."""

from __future__ import annotations

from datetime import date as CalendarDate
from typing import TYPE_CHECKING, Annotated, Any, Literal
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
from rockygpt_brain.retrieval.models import TABLES, SearchQuery

if TYPE_CHECKING:
    from rockygpt_brain.retrieval.data import CampusData


def _trimmed(value: str) -> str:
    if value != value.strip() or not value.strip():
        raise ValueError("Identity text must be nonempty and already trimmed")
    return value


IdentityText = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=240), AfterValidator(_trimmed)
]


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


class ProfileQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str | None = Field(default=None, min_length=1, max_length=240)
    entity_id: UUID | None = None
    include: list[Literal["contact", "hours"]] = Field(min_length=1, max_length=2)
    date: CalendarDate | None = Field(
        default=None, description="Campus-local service date; null means the current campus date."
    )

    @model_validator(mode="after")
    def valid_selector(self) -> ProfileQuery:
        if (self.entity is None) == (self.entity_id is None):
            raise ValueError("Supply exactly one published name/alias or persistent entity_id")
        if self.entity is not None and not self.entity.strip():
            raise ValueError("entity must not be blank")
        if len(set(self.include)) != len(self.include):
            raise ValueError("include must not contain duplicates")
        return self


class IdentityLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    collection: Literal["contacts", "campus_hours"]
    source_key: IdentityText
    source_record_keys: list[IdentityText] = Field(min_length=1, max_length=64)


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    kind: Literal["office", "person", "facility", "venue"]
    name: IdentityText
    aliases: list[IdentityText] = Field(max_length=32)
    links: list[IdentityLink] = Field(min_length=1, max_length=16)

    @field_validator("id", mode="before")
    @classmethod
    def canonical_id(cls, value: Any) -> Any:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("Identity ID must be a lowercase canonical UUID")
        return value


class IdentityRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    entities: list[Identity] = Field(max_length=1000)

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
            key = _json(value)
            values.setdefault(key, {"value": value, "evidence_ids": []})["evidence_ids"].append(
                record["id"]
            )
        coverage[field] = (
            "conflict" if len(values) > 1 else "published" if values else "not_published"
        )
        if len(values) > 1:
            conflicts[field] = list(values.values())
    return coverage, conflicts


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
    for component in query.include:
        collection = "contacts" if component == "contact" else "campus_hours"
        links = [link for link in entity.links if link.collection == collection]
        records: list[dict[str, Any]] = []
        missing_keys: list[str] = []
        failed_links = 0
        truncated = False
        for link in links:
            try:
                rows = data._fetch(
                    sql.SQL(
                        "SELECT t.*, t.id::text AS id, t.source_id::text AS source_id, "
                        "count(*) OVER() AS total FROM rockygpt_v2.{table} t "
                        "JOIN rockygpt_v2.sources s ON s.id=t.source_id "
                        "WHERE t.dataset_version_id=%s::uuid AND s.source_key=%s "
                        "AND t.source_record_key=ANY(%s) "
                        "AND s.trust_tier IN ('official_primary','official_secondary') "
                        "ORDER BY t.id LIMIT 101"
                    ).format(table=sql.Identifier(TABLES[collection][0])),
                    (data.dataset["id"], link.source_key, link.source_record_keys),
                )
                found = {row["source_record_key"] for row in rows}
                missing_keys.extend(key for key in link.source_record_keys if key not in found)
                truncated = truncated or bool(rows and rows[0]["total"] > len(rows))
                for row in rows:
                    fields = {
                        key: row[key] for key in TABLES[collection][1] if row.get(key) is not None
                    }
                    record = data._evidence(collection, row, fields, str(row["name"]))
                    if record is None:
                        missing_keys.append(row["source_record_key"])
                        continue
                    record["canonical_entity_id"] = str(entity.id)
                    if component == "hours":
                        record["fields"]["availability_scope"] = "unspecified"
                        record["limitations"].append(
                            "Published operating schedule with unspecified availability scope. "
                            "It does not establish staff, service-desk, facility or telephone "
                            "availability separately; never infer phone-answering hours."
                        )
                    records.append(record)
            except Exception:
                # An unavailable link must not erase another independently retrieved component.
                failed_links += 1
        if component == "contact":
            target = query.date or data.today
            records = data._dates(
                records, SearchQuery(collection="contacts", date_from=target, date_to=target)
            )
        else:
            # Exceptions supersede a source's own regular schedule, not a
            # different source's conflicting schedule for the same identity.
            by_source: dict[str, list[dict[str, Any]]] = {}
            for record in records:
                by_source.setdefault(record["source_key"], []).append(record)
            records = [
                record
                for source_records in by_source.values()
                for record in data._dates(
                    source_records,
                    SearchQuery(collection="campus_hours", date_from=query.date or data.today),
                )
            ]
        records = list({record["id"]: record for record in records}.values())
        field_names = (
            tuple(field for field in TABLES["contacts"][1] if field != "name")
            if component == "contact"
            else ("hours", "schedule")
        )
        coverage, conflicts = _field_coverage(records, field_names)
        for record in records:
            if conflicts:
                record["coverage"]["fields"].update({field: "conflict" for field in conflicts})
                record["limitations"].append(
                    "Linked records disagree on "
                    + ", ".join(conflicts)
                    + "; identity does not establish which value is authoritative."
                )
            data._seen[record["id"]] = record
        result["components"][component] = {
            "status": (
                "partial"
                if records and (failed_links or missing_keys or truncated)
                else "available"
                if records
                else "unavailable"
                if failed_links
                else "missing"
            ),
            "evidence_ids": [record["id"] for record in records],
            "fields": coverage,
            "conflicts": conflicts,
            "linked_records_missing": len(set(missing_keys)),
            "failed_links": failed_links,
            "truncated": truncated,
        }
        if component == "hours":
            result["components"][component].update(
                service_date=(query.date or data.today).isoformat(),
                availability_scope="unspecified",
            )
        result["records"].extend(data._public(record) for record in records)
        result["truncated"] = result["truncated"] or truncated
    result["total_matches"] = len(result["records"])
    if not result["records"] and any(
        component["status"] == "unavailable" for component in result["components"].values()
    ):
        result["status"] = "unavailable"
    return result
