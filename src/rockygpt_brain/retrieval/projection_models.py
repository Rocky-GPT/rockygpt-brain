"""Graph projection v2 contract; source records are not canonical entities.

Each response lists the source records it reads once, in `sources`. An assertion
names its source record and the field it was read from; record-level caveats
(freshness, a collection's limitations) live on the source record, and only
field-specific caveats stay on the assertion.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROJECTION_VERSION = "linked-collections-2"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRecord(Contract):
    """One original published record: a database row, or an item of a release artifact."""
    id: str
    collection: str
    row_id: str
    source_key: str | None
    source_record_key: str | None
    source_url: str | None
    derived_from_source_id: str | None = None
    # Artifact-backed records: the artifact and the exact path of the original item.
    artifact_key: str | None = None
    artifact_path: list[str | int] | None = None
    collected_at: str | None
    valid_from: str | None
    valid_until: str | None
    freshness: Literal["fresh", "stale", "unknown", "static"]
    limitations: list[str]


class Assertion(Contract):
    id: str
    value: Any
    source_id: str
    field_path: list[str | int]
    limitations: list[str]
    publication_status: Literal["published", "not_published", "unspecified"] = "unspecified"


class Property(Contract):
    key: str
    label: str
    value_type: str
    assertions: list[Assertion]


class Entity(Contract):
    id: str
    kind: str
    name: str
    aliases: list[str]
    status: str | None = None


class EntitySubject(Contract):
    kind: Literal["entity"] = "entity"
    entity_id: str


class RecordSubject(Contract):
    kind: Literal["record"] = "record"
    record_id: str


class RegistryLocator(Contract):
    identity_hash: str
    entity_id: str
    relationship_index: int = Field(ge=0)


class Relationship(Contract):
    id: str
    subject: EntitySubject | RecordSubject = Field(discriminator="kind")
    predicate: str
    target_entity_id: str
    direction: Literal["outgoing", "incoming"]
    # Original release evidence references, including optional pinned row IDs.
    # These are references, not a claim that the graph reverified campus facts.
    evidence: list[dict[str, Any]]
    registry_locator: RegistryLocator


class ContextualRecord(Contract):
    id: str
    label: str
    record_type: str
    source_id: str
    context: list[Property]
    properties: list[Property]
    relationships: list[Relationship] = Field(default_factory=list)


class RecordGroup(Contract):
    key: str
    label: str
    record_type: str
    records: list[ContextualRecord]
    total: int
    returned: int
    next_cursor: str | None
    filters: dict[str, str | None]
    filter_fields: list[str]
    ordering: str


class CoverageIssue(Contract):
    reason: str
    collection: str | None = None
    record_id: str | None = None
    fields: list[str] = Field(default_factory=list)
    detail: str | None = None


class EntityProjection(Contract):
    schema_version: Literal[2] = 2
    projection_version: str = PROJECTION_VERSION
    dataset_version: str
    identity_hash: str
    entity: Entity
    selected_record_group: str | None = None
    properties_complete: bool
    properties: list[Property]
    record_groups: list[RecordGroup]
    relationships: list[Relationship]
    sources: list[SourceRecord]
    coverage: list[CoverageIssue]

    @model_validator(mode="after")
    def sources_resolve(self) -> EntityProjection:
        """Every assertion and record names a source record in this response, listed once."""
        listed = [source.id for source in self.sources]
        if len(set(listed)) != len(listed):
            raise ValueError("Source records must be listed once")
        known = set(listed)
        records = [record for group in self.record_groups for record in group.records]
        referenced = {assertion.source_id for prop in [
            *self.properties,
            *(prop for record in records for prop in (*record.context, *record.properties)),
        ] for assertion in prop.assertions} | {record.source_id for record in records}
        if not referenced <= known:
            raise ValueError("Assertions must reference a listed source record")
        return self
