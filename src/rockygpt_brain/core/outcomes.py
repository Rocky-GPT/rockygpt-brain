"""The Worker's output: one discriminated union.

The Writer sees nothing else, so every question the Writer might otherwise have
to answer for itself must already be settled by the variant it receives. See
`spec/brain-contract.md` sections 6 and 7.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypeAlias

from pydantic import Field

from rockygpt_brain.core.interpretation import (
    MEASUREMENT_RELATIONS,
    Cardinality,
    Relation,
    StrictModel,
)


class AbsenceCause(StrEnum):
    """Contract 7.2. These are not interchangeable and none describes the world."""

    ENTITY_UNKNOWN = "entity_unknown"
    NO_QUALIFYING_RECORDS = "no_qualifying_records"
    NO_SUPPORTING_EVIDENCE = "no_supporting_evidence"
    NO_CAPABILITY = "no_capability"
    OUT_OF_SCOPE = "out_of_scope"
    #: Records exist, but the returned set is truncated, so a relation defined
    #: over the whole set cannot be computed from it. The capability is not
    #: missing and the records are not missing; the guarantee is.
    INCOMPLETE_SOURCE = "incomplete_source"


class WithheldCause(StrEnum):
    POLICY_WRITE = "policy_write"
    POLICY_PERSONAL = "policy_personal"
    POLICY_SECRET = "policy_secret"  # noqa: S105 — a refusal cause, not a credential


class Success(StrictModel):
    outcome: Literal["success"] = "success"
    relation: Relation
    cardinality: Cardinality
    records: list[dict[str, Any]] = Field(default_factory=list)
    value: int | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    resolved: dict[str, Any] = Field(default_factory=dict)


class Absent(StrictModel):
    outcome: Literal["absent"] = "absent"
    cause: AbsenceCause
    resolved: dict[str, Any] = Field(default_factory=dict)


class Withheld(StrictModel):
    """Text is assembled in code. The Writer passes it through unchanged."""

    outcome: Literal["withheld"] = "withheld"
    cause: WithheldCause
    text: str


class Unavailable(StrictModel):
    outcome: Literal["unavailable"] = "unavailable"
    dependency: str


class Clarify(StrictModel):
    outcome: Literal["clarify"] = "clarify"
    missing: list[str] = Field(min_length=1)
    pending_request: dict[str, Any] = Field(default_factory=dict, alias="pendingRequest")


class Error(StrictModel):
    outcome: Literal["error"] = "error"
    code: str


class General(StrictModel):
    """A non-institutional question.

    The only variant the Writer may answer from its own knowledge, and only
    because the Listener classified the scope as `world`. `current_time` is
    authoritative: even here the Writer does not read a clock.
    """

    outcome: Literal["general"] = "general"
    question: str
    current_time: str = Field(alias="currentTime")


Outcome: TypeAlias = Success | Absent | Withheld | Unavailable | Clarify | Error | General


#: Contract 6.1. Higher governs. Composition takes the maximum, so a weaker
#: operation can never lift a stronger one to success.
_PRECEDENCE: dict[str, int] = {
    "general": 0,
    "success": 1,
    "absent": 2,
    "clarify": 3,
    "withheld": 4,
    "unavailable": 5,
    "error": 6,
}


def precedence(outcome: Outcome) -> int:
    return _PRECEDENCE[outcome.outcome]


def strongest(outcomes: list[Outcome]) -> Outcome:
    """The governing outcome of a composed claim.

    Returns the highest-precedence member. When every member is `success` the
    caller owns the merge: this function only guarantees that a composition can
    never report success unless all of its inputs did.
    """

    if not outcomes:
        raise ValueError("composition requires at least one operation result")
    return max(outcomes, key=precedence)


class CardinalityViolation(Exception):
    """The Worker finished with more records than the request denotes."""


def seal(outcome: Outcome) -> Outcome:
    """Assert the contract's success invariants before OUT leaves the Worker.

    Contract 6.2 and 7.1. A violation is a Worker defect: it returns `error`
    rather than a longer list, because a longer list is exactly what the Writer
    would then have to choose from.
    """

    if not isinstance(outcome, Success):
        return outcome

    measured = outcome.relation in MEASUREMENT_RELATIONS
    if not outcome.records and not measured:
        raise CardinalityViolation(
            f"success with no records requires a measurement relation, got {outcome.relation}"
        )
    if measured and outcome.value is None and not outcome.records:
        raise CardinalityViolation("measurement success must carry a value")
    if outcome.cardinality is Cardinality.ONE and len(outcome.records) > 1:
        raise CardinalityViolation(f"cardinality one sealed with {len(outcome.records)} records")
    return outcome
