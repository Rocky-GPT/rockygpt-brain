"""Additive graph projection v1 contract; source records are not canonical entities."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROJECTION_VERSION = "dining-contact-1"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RowLocator(Contract):
    kind: Literal["row"] = "row"
    collection: str
    row_id: str
    field_path: list[str | int]


class Provenance(Contract):
    source_key: str
    source_record_key: str
    source_url: str | None
    locator: RowLocator
    collected_at: str | None
    valid_from: str | None
    valid_until: str | None
    freshness: Literal["fresh", "stale", "unknown", "static"]


class Assertion(Contract):
    id: str
    value: Any
    provenance: list[Provenance]
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
    schema_version: Literal[1] = 1
    projection_version: str = PROJECTION_VERSION
    dataset_version: str
    identity_hash: str
    entity: Entity
    selected_record_group: str | None = None
    properties_complete: bool
    properties: list[Property]
    record_groups: list[RecordGroup]
    relationships: list[Relationship]
    coverage: list[CoverageIssue]
