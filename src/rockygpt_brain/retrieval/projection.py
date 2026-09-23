"""Explicit collection mappings over exact graph readers; never name-based joins.

Stage one maps direct contact assertions and contextual dining records. Everything
else remains available through the unchanged properties API with explicit coverage.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, ValidationError

from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.knowledge import KnowledgeGraph
from rockygpt_brain.retrieval.projection_models import (
    PROJECTION_VERSION,
    Assertion,
    ContextualRecord,
    Contract,
    CoverageIssue,
    Entity,
    EntityProjection,
    EntitySubject,
    Property,
    Provenance,
    RecordGroup,
    RegistryLocator,
    Relationship,
    RowLocator,
)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    source: str
    value_type: str = "text"


@dataclass(frozen=True)
class RecordSpec:
    collection: str
    key: str
    label: str
    context: tuple[FieldSpec, ...]
    properties: tuple[FieldSpec, ...]
    filters: tuple[str, ...]
    ignored: frozenset[str] = frozenset()


RECORD_SPECS = (
    RecordSpec("menu", "menu_offerings", "Menu offerings", (
        FieldSpec("valid_from", "valid_from", "date"),
        FieldSpec("valid_until", "valid_until", "date"),
        FieldSpec("meal", "meal"), FieldSpec("station", "station"),
    ), (
        FieldSpec("name", "name"), FieldSpec("calories", "calories", "number"),
        FieldSpec("portion_size", "portion_size"), FieldSpec("vegan", "vegan", "boolean"),
        FieldSpec("vegetarian", "vegetarian", "boolean"),
        FieldSpec("allergens", "allergens", "text_list"),
        FieldSpec("dietary_label_coverage", "label_coverage", "dietary_coverage"),
    ), ("date", "meal", "station")),
    RecordSpec("dining_hours", "dining_hours", "Dining hours", (
        FieldSpec("weekday", "day"), FieldSpec("valid_from", "valid_from", "date"),
        FieldSpec("valid_until", "valid_until", "date"),
    ), (FieldSpec("schedule", "schedule"),), ("day",), frozenset({"name"})),
)
CONTACT_FIELDS = tuple(FieldSpec(key, key) for key in (
    "name", "email", "phone", "title", "office", "department", "contact_note",
))
CONTACT_INTERNAL = frozenset({"search_text", "normalization_metadata", "raw_phone",
                              "phone_normalization_status"})
PROPERTY_RECORD_LIMIT = 100


def stable_id(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


class Cursor(Contract):
    projection_version: str
    dataset_version: str
    identity_hash: str
    entity_id: UUID
    group: str
    filters: dict[str, str | None]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=10_000_000)

    def encode(self) -> str:
        return base64.urlsafe_b64encode(self.model_dump_json().encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> Cursor:
        try:
            raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
            return cls.model_validate_json(raw)
        except (ValueError, binascii.Error, ValidationError):
            raise HTTPException(422, "Invalid projection cursor") from None


def validate_selection(group: str | None, filters: dict[str, Any], cursor: str | None) -> None:
    spec = next((s for s in RECORD_SPECS if s.key == group), None)
    if group is not None and spec is None:
        raise HTTPException(422, "Unknown projection record group")
    if (filters or cursor) and spec is None:
        raise HTTPException(422, "Filters and cursors require a record group")
    if spec and (not filters.keys() <= set(spec.filters) or any(
        value is not None and not isinstance(value, str) for value in filters.values()
    )):
        raise HTTPException(422, "Unsupported projection filter")
    if cursor:
        Cursor.decode(cursor)


def valid_value(value: Any, kind: str) -> bool:
    if value is None:
        return True
    if kind in {"text", "date"}:
        return isinstance(value, str)
    if kind == "number":
        return type(value) in {int, float}
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "text_list":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if kind == "dietary_coverage":
        return (isinstance(value, dict) and value.keys() <= {"vegan", "vegetarian", "allergens"}
                and all(v in {"published", "not_published"} for v in value.values()
                        if isinstance(v, str)) and all(isinstance(v, str) for v in value.values()))
    return False


class Projection:
    def __init__(self, data: Any, snapshot: dict[str, Any]) -> None:
        self.data = data
        self.snapshot = snapshot
        self.coverage: list[CoverageIssue] = []

    def _field(self, record: dict[str, Any], spec: FieldSpec) -> Property | None:
        raw = record["raw_record"]
        if spec.source not in raw:
            self.coverage.append(CoverageIssue(reason="field_unavailable",
                collection=record["collection"], record_id=record["id"], fields=[spec.source]))
            return None
        value = raw[spec.source]
        if not valid_value(value, spec.value_type):
            self.coverage.append(CoverageIssue(reason="unsupported_field_shape",
                collection=record["collection"], record_id=record["id"], fields=[spec.source]))
            return None
        # Reuse source freshness semantics without applying search normalization,
        # effective-date selection, or manufacturing missing collection dates.
        evidence = self.data._evidence(record["collection"], raw, {}, record["title"])
        if evidence is None:
            self.coverage.append(CoverageIssue(reason="source_unavailable",
                collection=record["collection"], record_id=record["id"]))
            return None
        provenance = Provenance(
            source_key=record["source_key"], source_record_key=record["source_record_key"],
            source_url=record["url"],
            locator=RowLocator(collection=record["collection"],
                               row_id=record["source_record_id"], field_path=[spec.source]),
            collected_at=evidence["collected_at"], valid_from=evidence["valid_from"],
            valid_until=evidence["valid_until"], freshness=evidence["freshness"],
        )
        limitations = [*evidence["limitations"]]
        publication_status: Literal["published", "not_published", "unspecified"] = "unspecified"
        if record["collection"] == "menu" and spec.source in {"vegan", "vegetarian", "allergens"}:
            labels = raw.get("label_coverage")
            status = labels.get(spec.source) if isinstance(labels, dict) else None
            if status == "published":
                publication_status = "published"
            elif status == "not_published":
                publication_status = "not_published"
            if status != "published":
                limitations.append("This dietary field is not marked published by the source; "
                                   "an empty list or false value does not establish absence.")
        if record["collection"] == "dining_hours":
            limitations.append("Published schedule record; applicability and seasonal precedence "
                               "have not been resolved for a selected service date.")
        return Property(key=spec.key, label=spec.key.replace("_", " "), value_type=spec.value_type,
            assertions=[Assertion(id=stable_id(self.snapshot["dataset_version"], record["id"],
                spec.source), value=value, provenance=[provenance], limitations=limitations,
                publication_status=publication_status)])

    def _fields(self, record: dict[str, Any], specs: tuple[FieldSpec, ...]) -> list[Property]:
        return [prop for spec in specs if (prop := self._field(record, spec)) is not None]

    def _unmapped(self, record: dict[str, Any], specs: tuple[FieldSpec, ...],
                  ignored: frozenset[str]) -> None:
        fields = sorted(record["fields"].keys() - {s.source for s in specs} - ignored)
        if fields:
            self.coverage.append(CoverageIssue(reason="fields_not_migrated",
                collection=record["collection"], record_id=record["id"], fields=fields))

    def _records(self, reader: GraphData, collection: str, filters: dict[str, Any],
                 offset: int, limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        page = reader.browse(collection, filters, None, offset, limit)
        records = []
        for summary in page["records"]:
            try:
                records.append(reader.record(collection, summary["id"]))
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                self.coverage.append(CoverageIssue(reason="record_unavailable",
                    collection=collection, record_id=summary["id"]))
        return page, records

    def build(self, entity_id: UUID, group: str | None, filters: dict[str, Any],
              limit: int, cursor: str | None) -> EntityProjection:
        graph = KnowledgeGraph(self.data)
        index = graph.index()
        entity = next((e for e in index["nodes"] if e["id"] == str(entity_id)), None)
        if entity is None:
            raise HTTPException(404, "Campus entity was not found")
        identity = next((e for e in graph.registry.entities if e.id == entity_id), None)
        collections = {link.collection for link in identity.links} if identity else {"courses"}
        selected = next((s for s in RECORD_SPECS if s.key == group), None)
        if selected and selected.collection not in collections:
            raise HTTPException(422, "Record group is not linked to this entity")
        scope = dict(projection_version=PROJECTION_VERSION,
                     dataset_version=self.snapshot["dataset_version"],
                     identity_hash=self.snapshot["identity_hash"], entity_id=entity_id,
                     group=group or "", filters=filters, limit=limit)
        offset = 0
        if cursor:
            decoded = Cursor.decode(cursor)
            if decoded.model_dump(exclude={"offset"}) != scope:
                raise HTTPException(409, "Projection cursor scope changed; restart record group")
            offset = decoded.offset
        reader = GraphData(self.data, entity_id) if identity else None
        properties: dict[str, Property] = {}
        groups = []
        properties_complete = group is None and collections <= {"contacts", "menu", "dining_hours"}
        if reader and "contacts" in collections and group is None:
            page, records = self._records(reader, "contacts", {}, 0, PROPERTY_RECORD_LIMIT)
            for record in records:
                for prop in self._fields(record, CONTACT_FIELDS):
                    if prop.key in properties:
                        properties[prop.key].assertions.extend(prop.assertions)
                    else:
                        properties[prop.key] = prop
                self._unmapped(record, CONTACT_FIELDS, CONTACT_INTERNAL)
            if page["next_offset"] is not None:
                properties_complete = False
                self.coverage.append(CoverageIssue(reason="property_limit", collection="contacts",
                    detail="Only the first 100 source records are projected; use the legacy reader "
                           "for the remaining direct assertions."))
        for spec in RECORD_SPECS:
            if not reader or spec.collection not in collections or (group and spec.key != group):
                continue
            page, records = self._records(reader, spec.collection, filters, offset, limit)
            projected = []
            for record in records:
                self._unmapped(record, (*spec.context, *spec.properties), spec.ignored)
                projected.append(ContextualRecord(
                    id=stable_id(self.snapshot["dataset_version"], record["id"]),
                    label=record["title"], record_type=spec.key,
                    context=self._fields(record, spec.context),
                    properties=self._fields(record, spec.properties)))
            next_cursor = (Cursor(**{**scope, "group": spec.key},
                                  offset=page["next_offset"]).encode()
                           if page["next_offset"] is not None else None)
            groups.append(RecordGroup(key=spec.key, label=spec.label, record_type=spec.key,
                records=projected, total=page["total"], returned=len(projected),
                next_cursor=next_cursor, filters=filters, filter_fields=list(spec.filters),
                ordering="published record title, then original row ID"))
        migrated = {"contacts", *(s.collection for s in RECORD_SPECS)}
        for collection in sorted(collections - migrated):
            self.coverage.append(CoverageIssue(reason="collection_not_migrated",
                                               collection=collection))
        if reader:
            for diagnostic in reader.diagnostics:
                self.coverage.append(CoverageIssue(reason=diagnostic["reason"],
                                                   collection=diagnostic.get("collection")))
        relationships = []
        # Repeated identical declarations are separate occurrences, each located
        # by its own array index (as in the graph export), never the first match.
        claimed: dict[str, set[int]] = {}
        for edge in index["edges"]:
            if str(entity_id) not in {edge["source"], edge["target"]}:
                continue
            owner = next(e for e in graph.registry.entities if str(e.id) == edge["source"])
            used = claimed.setdefault(edge["source"], set())
            relationship_index = next(i for i, r in enumerate(owner.relationships)
                if i not in used and r.type == edge["type"] and (
                    str(r.target_entity_id) if r.target_entity_id else
                    graph.course_identity(
                        r.target_record.source_key, r.target_record.source_record_key
                    ) if r.target_record else None
                ) == edge["target"] and [ref.model_dump(mode="json", exclude_none=True)
                                         for ref in r.evidence] == edge["evidence"])
            used.add(relationship_index)
            relationships.append(Relationship(
                id=stable_id(self.snapshot["identity_hash"], edge["source"], relationship_index),
                subject=EntitySubject(entity_id=edge["source"]), predicate=edge["type"],
                target_entity_id=edge["target"],
                direction="outgoing" if edge["source"] == str(entity_id) else "incoming",
                evidence=edge["evidence"], registry_locator=RegistryLocator(
                    identity_hash=self.snapshot["identity_hash"],
                    entity_id=edge["source"], relationship_index=relationship_index)))
        if any(issue.collection == "contacts" for issue in self.coverage):
            properties_complete = False
        return EntityProjection(dataset_version=self.snapshot["dataset_version"],
            identity_hash=self.snapshot["identity_hash"], entity=Entity(**entity),
            selected_record_group=group, properties_complete=properties_complete,
            properties=list(properties.values()), record_groups=groups,
            relationships=relationships, coverage=self.coverage)
