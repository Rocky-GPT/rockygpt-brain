"""Explicit collection mappings over exact graph readers; never name-based joins.

Every collection an identity can link is mapped field by field. Records that
describe the entity itself (a directory entry, a faculty profile, a catalog
program) become properties; collections of repeated records (menu offerings,
dining and operating hours) become contextual record groups. A field outside a
mapping is reported as coverage, never copied blindly, and a value whose shape
is not the declared one is withheld.
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

from rockygpt_brain.retrieval.graph import ARTIFACT_COLLECTIONS, GraphData
from rockygpt_brain.retrieval.knowledge import ReleaseGraph, release_graph
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
    RecordGroup,
    RegistryLocator,
    Relationship,
    SourceRecord,
)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    source: str
    value_type: str = "text"


def fields(*specs: str | tuple[str, str] | tuple[str, str, str]) -> tuple[FieldSpec, ...]:
    """`"name"`, `("key", "value_type")` or `("key", "source", "value_type")`."""
    return tuple(
        FieldSpec(spec, spec) if isinstance(spec, str)
        else FieldSpec(spec[0], spec[0], spec[1]) if len(spec) == 2
        else FieldSpec(spec[0], spec[1], spec[2])
        for spec in specs
    )


@dataclass(frozen=True)
class PropertySpec:
    """A collection whose records describe the linked entity itself."""
    collection: str
    fields: tuple[FieldSpec, ...]
    # Stored for search, processing or this repository's own use; not published facts.
    ignored: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RecordSpec:
    """A collection of repeated records, each a context plus its own properties."""
    collection: str
    key: str
    label: str
    context: tuple[FieldSpec, ...]
    properties: tuple[FieldSpec, ...]
    filters: tuple[str, ...]
    ignored: frozenset[str] = frozenset()
    # A caveat about every record in the group, kept on each record's source.
    limitation: str | None = None


SCHEDULE_LIMITATION = ("Published schedule record; applicability and seasonal precedence have "
                       "not been resolved for a selected service date.")
PROPERTY_SPECS = (
    PropertySpec("contacts", fields(
        "name", "type", "title", "status", "department", "email", "phone",
        ("phones", "phone_list"), "office", ("offices", "text_list"), "preferred_contact",
        ("prefers_email", "boolean"), "contact_note", ("contact_aliases", "aliases", "text_list"),
    ), frozenset({"search_text", "normalization_metadata", "raw_phone",
                  "phone_normalization_status"})),
    PropertySpec("faculty", fields(
        "name", "title", "school", "email", "phone", "office",
        ("profile_url", "profileUrl", "url"), ("image_url", "imageUrl", "url"), "bio",
        ("education", "text_list"), ("profile_courses", "courses", "text_list"),
        ("teaching_interests", "teachingInterests", "text_list"),
        ("research_interests", "researchInterests", "text_list"),
        ("published_research", "publishedResearch", "text_list"),
        # imagePath is this repository's former local copy of the photo, not a Ramapo value.
    ), frozenset({"imagePath"})),
    PropertySpec("programs", fields(
        "name", "degree", "program_kind", "school", "description", ("program_url", "url"),
    )),
    PropertySpec("clubs", fields("name", "category", ("website_url", "url"))),
    PropertySpec("events", fields(
        "title", "date_label", ("starts_at", "datetime"), "start_time", "end_time",
        "organizer", "description", ("event_url", "url"),
    )),
    PropertySpec("buildings", fields(
        "name", "category", ("map_url", "url"), ("room_prefixes", "text_list"), "concept3d_id",
        ("identity_basis", "basis", "text"),
    )),
    PropertySpec("schools", fields(
        "name", "abbreviation", ("official_url", "url", "url"), "section",
        ("former_names", "legacy_names", "former_names"),
    )),
    PropertySpec("courses", fields(
        "code", "name", "description", ("credits", "credits"), ("attributes", "text_list"),
    )),
    PropertySpec("subjects", fields(
        "code", "name", "display_name", ("search_terms", "text_list"),
        ("course_count", "number"),
    )),
)
RECORD_SPECS = (
    RecordSpec("menu", "menu_offerings", "Menu offerings", fields(
        ("valid_from", "date"), ("valid_until", "date"), "meal", "station",
    ), fields(
        "name", ("calories", "number"), "portion_size", ("vegan", "boolean"),
        ("vegetarian", "boolean"), ("allergens", "text_list"),
        ("dietary_label_coverage", "label_coverage", "dietary_coverage"),
    ), ("date", "meal", "station")),
    RecordSpec("dining_hours", "dining_hours", "Dining hours", fields(
        ("weekday", "day", "text"), ("valid_from", "date"), ("valid_until", "date"),
    ), fields("schedule"), ("day",), frozenset({"name"}), SCHEDULE_LIMITATION),
    RecordSpec("campus_hours", "operating_hours", "Operating hours", fields(
        ("weekday", "day", "text"), ("valid_from", "date"), ("valid_until", "date"),
    ), fields("schedule", ("hours", "hours_list"), "notes"), ("day",),
        frozenset({"name", "source_url"}),
        SCHEDULE_LIMITATION),
)
MAPPED = {spec.collection for spec in (*PROPERTY_SPECS, *RECORD_SPECS)}
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


def _objects(value: Any, shapes: dict[str, tuple[type, ...]], required: set[str]) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict) and required <= item.keys() <= shapes.keys()
        and all(isinstance(v, shapes[k]) and not isinstance(v, bool) for k, v in item.items())
        for item in value)


def valid_value(value: Any, kind: str) -> bool:
    """Whether a published value has the declared shape; nested keys are an allowlist."""
    if value is None:
        return True
    if kind in {"text", "date", "datetime", "url"}:
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
    if kind == "phone_list":
        return (_objects(value, {key: (str,) for key in ("type", "number", "extension")},
                         set()) and all("number" in item or "extension" in item for item in value))
    if kind == "hours_list":
        return _objects(value, {"open": (str,), "close": (str,), "close_day_offset": (int,)},
                        {"open", "close"})
    if kind == "former_names":
        return _objects(value, {"name": (str,), "evidence": (str,)}, {"name", "evidence"})
    if kind == "credits":
        return type(value) in {int, float} or (
            isinstance(value, dict) and value.keys() <= {"min", "max", "operator"}
            and all(type(value.get(k)) in {int, float} for k in ("min", "max") if k in value)
            and isinstance(value.get("operator", ""), str))
    return False


class Projection:
    def __init__(self, data: Any, snapshot: dict[str, Any], *,
                 version: str | None = None) -> None:
        self.data = data
        self.snapshot = snapshot
        self.version = version or PROJECTION_VERSION
        self.coverage: list[CoverageIssue] = []
        self.sources: dict[str, SourceRecord] = {}
        self.source_records: dict[str, dict[str, Any]] = {}

    def _issue(self, reason: str, record: dict[str, Any], **detail: Any) -> None:
        self.coverage.append(CoverageIssue(reason=reason, collection=record["collection"],
                                           record_id=record["id"], **detail))

    def _source(self, record: dict[str, Any], limitation: str | None) -> str | None:
        """List the record's source once; its freshness and caveats cover every field."""
        identifier = str(record["id"])
        self.source_records[identifier] = record
        if identifier in self.sources:
            return identifier
        if record["collection"] in ARTIFACT_COLLECTIONS:
            # Parsed with the release's own capture time for the artifact.
            evidence: dict[str, Any] | None = record if record.get("freshness") else None
        else:
            # Reuse source freshness semantics without applying search normalization,
            # effective-date selection, or manufacturing missing collection dates.
            evidence = self.data._evidence(record["collection"], record["raw_record"], {},
                                           record["title"])
        if evidence is None:
            self._issue("source_unavailable", record)
            return None
        self.sources[identifier] = SourceRecord(
            id=identifier, collection=record["collection"],
            row_id=record["source_record_id"], source_key=record.get("source_key"),
            source_record_key=record.get("source_record_key"), source_url=evidence.get("url"),
            artifact_key=record.get("artifact_key"), artifact_path=record.get("artifact_path"),
            collected_at=evidence.get("collected_at"), valid_from=evidence.get("valid_from"),
            valid_until=evidence.get("valid_until"), freshness=evidence["freshness"],
            limitations=[*evidence.get("limitations", []), *([limitation] if limitation else [])],
        )
        return identifier

    def _field(self, record: dict[str, Any], source: str, spec: FieldSpec) -> Property | None:
        raw = record["raw_record"]
        if spec.source not in raw:
            self._issue("field_unavailable", record, fields=[spec.source])
            return None
        value = raw[spec.source]
        if not valid_value(value, spec.value_type):
            self._issue("unsupported_field_shape", record, fields=[spec.source])
            return None
        limitations = []
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
        return Property(key=spec.key, label=spec.key.replace("_", " "), value_type=spec.value_type,
            assertions=[Assertion(id=f"{source}#{spec.source}", value=value, source_id=source,
                field_path=[spec.source], limitations=limitations,
                publication_status=publication_status)])

    def _fields(self, record: dict[str, Any], source: str,
                specs: tuple[FieldSpec, ...]) -> list[Property]:
        return [prop for spec in specs if (prop := self._field(record, source, spec)) is not None]

    def _unmapped(self, record: dict[str, Any], specs: tuple[FieldSpec, ...],
                  ignored: frozenset[str]) -> None:
        unmapped = sorted(record["fields"].keys() - {s.source for s in specs} - ignored)
        if unmapped:
            self._issue("fields_not_migrated", record, fields=unmapped)

    def _page(self, reader: GraphData, collection: str, filters: dict[str, Any], offset: int,
              limit: int, course: tuple[str, str, str] | None) -> dict[str, Any]:
        if course is None:
            return reader.records(collection, filters, offset, limit)
        # A catalog course node has exactly one record, and no identity links to scope.
        records = [reader.record("courses", course[2])][offset:offset + limit]
        return {"records": records, "total": 1,
                "next_offset": None}

    def _relationships(self, graph: ReleaseGraph, entity_id: str) -> list[Relationship]:
        # Repeated identical declarations are separate occurrences, each located by
        # its own array index (as in the graph export), never the first match.
        course_ids = {(source, key): course for course, (source, key, _) in graph.courses.items()}
        relationships = []
        claimed: dict[str, set[int]] = {}
        for edge in graph.index["edges"]:
            if entity_id not in {edge["source"], edge["target"]}:
                continue
            owner = graph.identities[edge["source"]]
            used = claimed.setdefault(edge["source"], set())
            relationship_index = next(i for i, r in enumerate(owner.relationships)
                if i not in used and r.type == edge["type"] and (
                    str(r.target_entity_id) if r.target_entity_id else
                    course_ids.get((r.target_record.source_key, r.target_record.source_record_key))
                    if r.target_record else None
                ) == edge["target"] and [ref.model_dump(mode="json", exclude_none=True)
                                         for ref in r.evidence] == edge["evidence"])
            used.add(relationship_index)
            relationships.append(Relationship(
                id=stable_id(self.snapshot["identity_hash"], edge["source"], relationship_index),
                subject=EntitySubject(entity_id=edge["source"]), predicate=edge["type"],
                target_entity_id=edge["target"],
                direction="outgoing" if edge["source"] == entity_id else "incoming",
                evidence=edge["evidence"], registry_locator=RegistryLocator(
                    identity_hash=self.snapshot["identity_hash"],
                    entity_id=edge["source"], relationship_index=relationship_index)))
        return relationships

    def build(self, entity_id: UUID, group: str | None, filters: dict[str, Any],
              limit: int, cursor: str | None, *, include_records: bool = True) -> EntityProjection:
        graph = release_graph(self.data)
        node = graph.nodes.get(str(entity_id))
        if node is None:
            raise HTTPException(404, "Campus entity was not found")
        identity = graph.identities.get(str(entity_id))
        course = None if identity else graph.courses.get(str(entity_id))
        collections = ({link.collection for link in identity.links} if identity
                       else {"courses"} if course else set())
        selected = next((s for s in RECORD_SPECS if s.key == group), None)
        if selected and selected.collection not in collections:
            raise HTTPException(422, "Record group is not linked to this entity")
        scope = dict(projection_version=self.version,
                     dataset_version=self.snapshot["dataset_version"],
                     identity_hash=self.snapshot["identity_hash"], entity_id=entity_id,
                     group=group or "", filters=filters, limit=limit)
        offset = 0
        if cursor:
            decoded = Cursor.decode(cursor)
            if decoded.model_dump(exclude={"offset"}) != scope:
                raise HTTPException(409, "Projection cursor scope changed; restart record group")
            offset = decoded.offset
        reader = (GraphData(self.data, entity_id, registry=graph.registry) if identity
                  else GraphData(self.data))
        properties: dict[str, Property] = {}
        groups = []
        properties_complete = group is None
        for prop_spec in PROPERTY_SPECS if group is None else ():
            if prop_spec.collection not in collections:
                continue
            page = self._page(reader, prop_spec.collection, {}, 0, PROPERTY_RECORD_LIMIT, course)
            for record in page["records"]:
                self._unmapped(record, prop_spec.fields, prop_spec.ignored)
                source = self._source(record, None)
                if source is None:
                    continue
                for prop in self._fields(record, source, prop_spec.fields):
                    if prop.key in properties:
                        properties[prop.key].assertions.extend(prop.assertions)
                    else:
                        properties[prop.key] = prop
            if page["next_offset"] is not None:
                self.coverage.append(CoverageIssue(reason="property_limit",
                    collection=prop_spec.collection,
                    detail=f"Only the first {PROPERTY_RECORD_LIMIT} source records are "
                           "projected as properties."))
        for spec in RECORD_SPECS if include_records else ():
            if spec.collection not in collections or (group and spec.key != group):
                continue
            page = self._page(reader, spec.collection, filters, offset, limit, None)
            projected = []
            for record in page["records"]:
                self._unmapped(record, (*spec.context, *spec.properties), spec.ignored)
                source = self._source(record, spec.limitation)
                if source is None:
                    continue
                projected.append(ContextualRecord(
                    id=record["id"], label=record["title"], record_type=spec.key,
                    source_id=source, context=self._fields(record, source, spec.context),
                    properties=self._fields(record, source, spec.properties)))
            next_cursor = (Cursor(**{**scope, "group": spec.key},
                                  offset=page["next_offset"]).encode()
                           if page["next_offset"] is not None else None)
            groups.append(RecordGroup(key=spec.key, label=spec.label, record_type=spec.key,
                records=projected, total=page["total"], returned=len(projected),
                next_cursor=next_cursor, filters=filters, filter_fields=list(spec.filters),
                ordering="published record title, then original row ID"))
        for collection in sorted(collections - MAPPED):
            self.coverage.append(CoverageIssue(reason="collection_not_migrated",
                                               collection=collection))
        for diagnostic in reader.diagnostics:
            self.coverage.append(CoverageIssue(reason=diagnostic["reason"],
                                               collection=diagnostic.get("collection")))
        property_collections = {s.collection for s in PROPERTY_SPECS} | (collections - MAPPED)
        if any(issue.collection in property_collections for issue in self.coverage):
            properties_complete = False
        return EntityProjection(projection_version=self.version,
            dataset_version=self.snapshot["dataset_version"],
            identity_hash=self.snapshot["identity_hash"], entity=Entity(**node),
            selected_record_group=group, properties_complete=properties_complete,
            properties=list(properties.values()), record_groups=groups,
            relationships=self._relationships(graph, str(entity_id)),
            sources=list(self.sources.values()), coverage=self.coverage)
