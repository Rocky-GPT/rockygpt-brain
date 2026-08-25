"""What BRAIN #2 returns: the vocabulary a plan is written in.

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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from rockygpt_brain.brain.values import FieldName, Text
from rockygpt_brain.safety.schema import Concern

#: Words a filter value may carry instead of a date or an instant. Python
#: resolves them against the campus clock in `validate`, because the model is
#: never asked to do date arithmetic.
TIME_WORDS = ("now", "today", "tomorrow", "yesterday")


class Lane(StrEnum):
    """The three places an answer can come from.

    Two things that were once lanes are not, and for the same reason: a lane
    says where to look, and neither of them is a place.

    Recalling what was already said is not a lane. A question about the
    conversation is answered from the conversation, which BRAIN #3 is holding
    anyway — making it a lane meant routing to it from the one stage that had
    been denied sight of the conversation, which could not work. It is
    `Understanding.uses_context` instead.

    Someone at risk of harm is not a lane either. It is not a different place
    to look, it is a question that must be answered a particular way wherever
    the answer would have come from — and as a lane it had no executor, so the
    one turn that must never fail was the only one guaranteed to. It is
    `Plan.safety` instead, and Python acts on it before any lane runs.
    """

    CODE = "CODE"  # look it up in structured campus data
    RAG = "RAG"  # find it in a campus document
    GENERAL = "GENERAL"  # answer from what the model already knows


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
    """What to do about the question. BRAIN #1's second call.

    Built from an `Understanding.resolved` alone — never from the words the
    student typed. The two are separate calls so that is a fact about what the
    model can see rather than a line in an instruction it may or may not heed.

    Each lane uses the fields it needs and leaves the rest empty.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    #: Everything wrong with the question, and empty when nothing is.
    #:
    #: First on purpose. A structured response is generated field by field in
    #: the order they are declared, so asking this before `lane` is what makes
    #: it a judgement about the question rather than an afterthought to a
    #: routing decision already made.
    #:
    #: Orthogonal to the lane, and it overrides it. A question can name a real
    #: capability and still be one Rocky must not answer as asked, so the lane
    #: is planned as normal and Python acts on this before running it.
    safety: list[Concern] = Field(default_factory=list, max_length=4)
    lane: Lane
    capability: FieldName | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    operation: Operation = Field(default_factory=Operation)
    topic: Text | None = None  # RAG: what to look for in the documents
    #: GENERAL: whether the answer keeps. `stable` is true whenever it is
    #: asked; `current` changes, and has to be looked up now.
    freshness: Literal["stable", "current"] | None = None
    #: What a `current` GENERAL question means to look up. The planner's own
    #: words, kept as written.
    query: Text | None = None
    #: The query Python actually searches with: `query`, dated against the
    #: server clock unless it already carries a date of its own.
    #:
    #: Kept off the response schema, so the planner never sees it and never
    #: spends a token on it — `validate.check` is the only thing that sets it.
    #: Separate from `query` rather than overwriting it because they answer
    #: different questions: `query` is what the model meant to look up, and
    #: this is what was looked up. When a search comes back wrong, which of
    #: those two was at fault is the first thing worth knowing.
    effective_query: SkipJsonSchema[Text | None] = Field(default=None, alias="effectiveQuery")

    @property
    def filter_values(self) -> dict[str, str]:
        """The filters as the map they stand for. A repeated field keeps the last."""
        return {item.field: item.value for item in self.filters}

    def summary(self) -> dict[str, Any]:
        """The plan with its unused halves dropped — what a human reads in the log."""
        out: dict[str, Any] = {"lane": self.lane.value}
        # Leads when set. It overrides the lane, so it reads above it.
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
        # Only when it differs. On a query the planner already dated, printing
        # the same string twice says only that nothing happened.
        if self.effective_query and self.effective_query != self.query:
            out["effectiveQuery"] = self.effective_query
        return out
