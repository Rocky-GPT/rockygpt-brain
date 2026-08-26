from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from rockygpt_brain.brain.values import FieldName, Text
from rockygpt_brain.safety.schema import Concern

TIME_WORDS = ("now", "today", "tomorrow", "yesterday")


class Lane(StrEnum):
    CODE = "CODE"
    RAG = "RAG"
    GENERAL = "GENERAL"


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    field: FieldName
    value: Text


MOST_ROWS = 200


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    order_by: FieldName | None = Field(default=None, alias="orderBy")
    direction: Literal["ascending", "descending"] = "ascending"
    limit: int | None = Field(default=None, ge=1, le=MOST_ROWS)
    count: bool = False
    compare: list[FieldName] = Field(default_factory=list, max_length=4)

    @property
    def stated(self) -> bool:
        return bool(self.order_by or self.limit or self.count or self.compare)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    safety: list[Concern] = Field(default_factory=list, max_length=4)
    a_capability_answers_it: bool = Field(default=False, alias="aCapabilityAnswersIt")
    specific_to_ramapo: bool = Field(default=False, alias="specificToRamapo")
    lane: SkipJsonSchema[Lane] = Lane.GENERAL
    capability: FieldName | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    operation: Operation = Field(default_factory=Operation)
    topic: Text | None = None
    freshness: Literal["stable", "current"] | None = None
    query: Text | None = None
    effective_query: SkipJsonSchema[Text | None] = Field(default=None, alias="effectiveQuery")

    @property
    def filter_values(self) -> dict[str, str]:
        return {item.field: item.value for item in self.filters}

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "routing": {
                "CODE?": _yes(self.a_capability_answers_it),
                "RAMAPO?": (
                    NOT_ASKED if self.a_capability_answers_it else _yes(self.specific_to_ramapo)
                ),
                "ROUTE": self.lane.value,
            }
        }
        if self.safety:
            out = {"safety": [c.value for c in self.safety], **out}
        if self.freshness:
            out["freshness"] = self.freshness
        if self.capability:
            out["capability"] = self.capability
        if self.filters:
            out["filters"] = self.filter_values
        operation: dict[str, Any] = {}
        if self.operation.order_by:
            operation["orderBy"] = self.operation.order_by
            operation["direction"] = self.operation.direction
        if self.operation.limit is not None:
            operation["limit"] = self.operation.limit
        if self.operation.count:
            operation["count"] = True
        if self.operation.compare:
            operation["compare"] = list(self.operation.compare)
        if operation:
            out["operation"] = operation
        if self.topic:
            out["topic"] = self.topic
        if self.query:
            out["query"] = self.query
        if self.effective_query and self.effective_query != self.query:
            out["effectiveQuery"] = self.effective_query
        return out


NOT_ASKED = "—"


def _yes(answered: bool) -> str:
    return "Yes" if answered else "No"
