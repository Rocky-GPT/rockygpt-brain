"""The vocabulary a plan is written in.

Five lanes, a list of filters, and a handful of generic operations. Nothing
here names a question anyone might ask. This file describes what Rocky can be
asked to *do* — narrow, sort, limit, count, compare — not what anyone is
allowed to want.

The mistake this file exists to prevent is a growing list of intents. If a name
like `next_shuttle` or `menu_lookup` ever appears below, the translator has
quietly become a taxonomy, and every new question needs code again.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

FieldName = Annotated[
    str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
]
Text = Annotated[str, StringConstraints(min_length=1, max_length=200)]

#: Words a filter value may carry instead of a date or an instant. Python
#: resolves them against the campus clock in `validate`, because the model is
#: never asked to do date arithmetic.
TIME_WORDS = ("now", "today", "tomorrow", "yesterday")


class Lane(StrEnum):
    """The five things Rocky can do with a turn."""

    CODE = "CODE"  # look it up in structured campus data
    RAG = "RAG"  # find it in a campus document
    GENERAL = "GENERAL"  # answer from what the model already knows
    SAFETY = "SAFETY"  # the person may be at risk of harm
    MEMORY = "MEMORY"  # it was already said in this conversation


class Filter(BaseModel):
    """One narrowing: a field the capability allows, and the value to match.

    A list of pairs rather than a map, because a strict response schema cannot
    describe an object with arbitrary keys. `Plan.filter_values` gives back the
    map it stands for.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    field: FieldName
    value: Text


class Operation(BaseModel):
    """What to do with the rows the filters leave behind.

    Deliberately generic and deliberately short. Filtering is the `Filter` list
    on the plan; everything else Rocky can do to a result set is here.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    order_by: FieldName | None = Field(default=None, alias="orderBy")
    direction: Literal["ascending", "descending"] = "ascending"
    limit: int | None = Field(default=None, ge=1, le=50)
    count: bool = False
    compare: list[FieldName] = Field(default_factory=list, max_length=4)

    @property
    def stated(self) -> bool:
        """Whether anything was actually asked for.

        `direction` alone does not count: it has a default, so it is set on
        every operation whether or not one was meant. What makes an operation
        an operation is one of the other four.
        """
        return bool(self.order_by or self.limit or self.count or self.compare)


class Plan(BaseModel):
    """What AI #1 returns: one turn, translated into operations.

    Each lane uses the fields it needs and leaves the rest empty.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    #: First on purpose. A structured response is generated field by field in
    #: the order they are declared, so putting this before `lane` is what makes
    #: the model work out what is being asked before deciding how to answer it.
    #: Declared after `lane`, it is written as an afterthought and comes back
    #: as the question echoed verbatim.
    #:
    #: The question with everything it refers to filled in: "population of it"
    #: becomes "population of Paris". Stated on every plan, including when it
    #: is the question unchanged — an absent one would mean both "nothing to
    #: resolve" and "the planner did not bother", and those are different.
    resolved: Text | None = None
    lane: Lane
    capability: FieldName | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    operation: Operation = Field(default_factory=Operation)
    topic: Text | None = None  # RAG: what to look for in the documents
    #: GENERAL: whether the answer keeps. `stable` is true whenever it is
    #: asked; `current` changes, and has to be looked up now.
    freshness: Literal["stable", "current"] | None = None
    #: What to look for. MEMORY searches the conversation with it; a `current`
    #: GENERAL question searches the web. The lane says where, this says what.
    query: Text | None = None

    @property
    def filter_values(self) -> dict[str, str]:
        """The filters as the map they stand for. A repeated field keeps the last."""
        return {item.field: item.value for item in self.filters}

    def summary(self) -> dict[str, Any]:
        """The plan with its unused halves dropped — what a human reads in the log."""
        out: dict[str, Any] = {"lane": self.lane.value}
        if self.resolved:
            out["resolved"] = self.resolved
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
        return out
