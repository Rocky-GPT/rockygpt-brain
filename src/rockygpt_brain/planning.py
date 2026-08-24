"""Bounded model outputs; the planner cannot create an open tool loop."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from rockygpt_brain.contracts import UiAction


class InternalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RouteMode(StrEnum):
    GENERAL = "general"
    CONVERSATION = "conversation"
    CAPABILITY = "capability"
    RAG = "rag"
    COMPOSITE = "composite"
    CLARIFY = "clarify"
    POLICY = "policy"


class ShuttleSelection(StrEnum):
    FIRST = "first"
    NEXT = "next"
    ALL = "all"
    CURRENT = "current"


class ShuttleTimeScope(StrEnum):
    FULL_DAY = "full_day"
    REMAINING = "remaining"
    AT_TIME = "at_time"


ServiceDay = Literal["weekday", "saturday", "sunday"]
ShortArgument = Annotated[str, StringConstraints(min_length=1, max_length=120)]


class ShuttleIntent(InternalModel):
    route: ShortArgument | None = None
    origin: ShortArgument | None = None
    destination: ShortArgument | None = None
    service_date: date | None = Field(default=None, alias="serviceDate")
    service_day: ServiceDay | None = Field(default=None, alias="serviceDay")
    selection: ShuttleSelection
    time_scope: ShuttleTimeScope = Field(alias="timeScope")
    limit: int | None = Field(default=None, ge=1, le=50)

    @model_validator(mode="after")
    def selection_matches_scope(self) -> ShuttleIntent:
        if self.service_day is not None and self.service_date is None:
            raise ValueError("serviceDay assertions require an explicit serviceDate")
        expected = {
            ShuttleSelection.FIRST: ShuttleTimeScope.FULL_DAY,
            ShuttleSelection.NEXT: ShuttleTimeScope.REMAINING,
            ShuttleSelection.CURRENT: ShuttleTimeScope.AT_TIME,
        }.get(self.selection)
        if expected is not None and self.time_scope != expected:
            raise ValueError(f"{self.selection.value} requires timeScope={expected.value}")
        return self


class CapabilityOperation(InternalModel):
    name: Literal["shuttle"]
    arguments: ShuttleIntent


class RoutePlan(InternalModel):
    mode: RouteMode
    operations: list[CapabilityOperation] = Field(default_factory=list, max_length=4)
    context_references: list[str] = Field(
        default_factory=list, alias="contextReferences", max_length=10
    )
    compare_current_truth: bool = Field(default=False, alias="compareCurrentTruth")
    clarification: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def mode_matches_operations(self) -> RoutePlan:
        uses_capability = self.mode in {RouteMode.CAPABILITY, RouteMode.COMPOSITE}
        if uses_capability and not self.operations:
            raise ValueError("capability and composite plans require an operation")
        if not uses_capability and self.operations:
            raise ValueError("only capability and composite plans may contain operations")
        if self.mode == RouteMode.CLARIFY and not self.clarification:
            raise ValueError("clarify plans require clarification text")
        return self


class ClaimKind(StrEnum):
    CAMPUS = "campus"
    CONVERSATION = "conversation"
    GENERAL = "general"
    POLICY = "policy"


class DraftClaim(InternalModel):
    text: str = Field(min_length=1, max_length=2000)
    kind: ClaimKind
    evidence_ids: list[str] = Field(alias="evidenceIds", max_length=12)


class AnswerDraft(InternalModel):
    answer: str = Field(min_length=1, max_length=8000)
    route: str = Field(min_length=1, max_length=64)
    claims: list[DraftClaim] = Field(max_length=20)
    citation_evidence_ids: list[str] = Field(
        default_factory=list, alias="citationEvidenceIds", max_length=3
    )
    ui_actions: list[UiAction] = Field(default_factory=list, alias="uiActions", max_length=6)
    suggested_questions: list[str] = Field(
        default_factory=list, alias="suggestedQuestions", max_length=3
    )


ROUTER_PROMPT_VERSION = "hybrid-v1-router-1"
DRAFT_PROMPT_VERSION = "hybrid-v1-draft-1"
