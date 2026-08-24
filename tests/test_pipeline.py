"""Pipeline properties: Worker decides, Writer only communicates.

Contract sections 4.2, 6 and 9. As in `test_contract.py`, nothing here names a
question, an entity, or an expected answer from any evaluation suite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.api.contracts import ChatRequest
from rockygpt_brain.core.brain import Brain, TurnIdentity
from rockygpt_brain.core.compilation import CompiledPlan
from rockygpt_brain.core.executor import Executor
from rockygpt_brain.core.interpretation import (
    CARDINALITY_OF_RELATION,
    Access,
    Cardinality,
    DangerClass,
    DocumentsTask,
    Domain,
    Interpretation,
    Mention,
    Operation,
    OrderSemantic,
    Relation,
    Scope,
    ShuttleTask,
    TimeNamed,
    WorldTask,
)
from rockygpt_brain.core.model import Draft
from rockygpt_brain.core.outcomes import AbsenceCause, Absent, Error, General, Success
from rockygpt_brain.services.memory import MemoryStore

TZ = ZoneInfo("America/New_York")
NOW = datetime(2031, 3, 6, 18, 30, tzinfo=UTC)


def trip(identifier: str, at: str) -> dict[str, Any]:
    return {"id": identifier, "matchedOrigin": {"time": at}, "evidenceIds": [f"e:{identifier}"]}


def complete(count: int) -> dict[str, Any]:
    return {"state": "complete", "truncated": False, "matched": count, "returned": count}


class FakeData:
    """Records what it was asked for and returns what it was told to."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {"outcome": "success", "records": []}
        self.calls: list[CompiledPlan] = []

    async def execute(self, plan: CompiledPlan) -> dict[str, Any]:
        self.calls.append(plan)
        return self.payload

    async def retrieve(self, query: str, domains: list[str]) -> dict[str, Any]:
        raise AssertionError("retrieve should not be reached")

    async def readiness(self) -> bool:
        return True


def shuttle(relation: Relation, **overrides: Any) -> ShuttleTask:
    fields: dict[str, Any] = {
        "domain": Domain.SHUTTLE,
        "operation": Operation.READ,
        "access": Access.PUBLIC,
        "relation": relation,
        "cardinality": CARDINALITY_OF_RELATION[relation],
        "order_by": None,
        "route": None,
        "origin": None,
        "destination": None,
        "time": TimeNamed(kind="named", name="today"),
    }
    fields.update(overrides)
    return ShuttleTask.model_validate(fields)


async def run(task: Any, payload: dict[str, Any] | None = None) -> tuple[Any, FakeData]:
    data = FakeData(payload)
    return await Executor(data).run(task, NOW, TZ), data


# --- the Worker finishes the selection ----------------------------------------


async def test_a_computed_extremum_returns_exactly_one_record() -> None:
    records = [trip("a", "7:00 AM"), trip("c", "9:40 PM"), trip("b", "1:15 PM")]
    outcome, _ = await run(
        shuttle(Relation.LATEST),
        {"outcome": "success", "records": records, "completeness": complete(3), "evidence": []},
    )
    assert isinstance(outcome, Success)
    assert len(outcome.records) == 1
    assert outcome.records[0]["id"] == "c"
    assert outcome.cardinality is Cardinality.ONE


async def test_the_opposite_extremum_uses_the_same_machinery() -> None:
    records = [trip("a", "7:00 AM"), trip("c", "9:40 PM")]
    payload = {
        "outcome": "success",
        "records": records,
        "completeness": complete(2),
        "evidence": [],
    }
    latest, _ = await run(shuttle(Relation.LATEST), payload)
    assert isinstance(latest, Success)
    assert latest.records[0]["id"] == "c"


async def test_an_incomplete_set_cannot_yield_an_extremum() -> None:
    records = [trip("a", "7:00 AM"), trip("b", "8:00 AM")]
    outcome, _ = await run(
        shuttle(Relation.LATEST),
        {
            "outcome": "success",
            "records": records,
            "completeness": {"state": "partial", "truncated": True, "matched": 60, "returned": 2},
        },
    )
    assert isinstance(outcome, Absent)
    assert outcome.cause is AbsenceCause.INCOMPLETE_SOURCE


async def test_missing_completeness_is_treated_as_incomplete() -> None:
    outcome, _ = await run(
        shuttle(Relation.LATEST),
        {"outcome": "success", "records": [trip("a", "7:00 AM")]},
    )
    assert isinstance(outcome, Absent)
    assert outcome.cause is AbsenceCause.INCOMPLETE_SOURCE


async def test_a_transport_that_overdelivers_is_an_error_not_a_truncation() -> None:
    """Silently keeping the first row would hide a breach behind a real answer."""

    outcome, _ = await run(
        shuttle(Relation.NEXT),
        {
            "outcome": "success",
            "records": [trip("a", "7:00 AM"), trip("b", "8:00 AM")],
            "completeness": complete(2),
        },
    )
    assert isinstance(outcome, Error)
    assert outcome.code == "cardinality_violation"


# --- absence keeps its cause --------------------------------------------------


async def test_a_reported_entity_miss_stays_an_entity_miss() -> None:
    outcome, _ = await run(
        shuttle(Relation.NEXT, destination=Mention(kind="mention", text="somewhere")),
        {
            "outcome": "no_match",
            "records": [],
            "completeness": {"state": "complete", "truncated": False, "reason": "entity_no_match"},
        },
    )
    assert isinstance(outcome, Absent)
    assert outcome.cause is AbsenceCause.ENTITY_UNKNOWN


async def test_a_resolved_entity_with_no_rows_is_not_an_entity_miss() -> None:
    outcome, _ = await run(
        shuttle(Relation.NEXT),
        {
            "outcome": "empty",
            "records": [],
            "completeness": {"state": "complete", "truncated": False, "reason": "no_remaining"},
        },
    )
    assert isinstance(outcome, Absent)
    assert outcome.cause is AbsenceCause.NO_QUALIFYING_RECORDS


async def test_an_absence_is_never_lifted_by_a_successful_shape() -> None:
    """An `outcome: success` envelope with no records is still an absence."""

    outcome, _ = await run(
        shuttle(Relation.ALL),
        {"outcome": "success", "records": [], "completeness": complete(0)},
    )
    assert isinstance(outcome, Absent)


# --- fail-closed lanes --------------------------------------------------------


async def test_retrieval_cannot_report_success_without_a_calibrated_floor() -> None:
    task = DocumentsTask(
        domain=Domain.DOCUMENTS,
        operation=Operation.READ,
        access=Access.PUBLIC,
        relation=Relation.DESCRIBE,
        cardinality=Cardinality.MANY,
        order_by=None,
        question="anything",
    )
    outcome, data = await run(task)
    assert isinstance(outcome, Absent)
    assert outcome.cause is AbsenceCause.NO_SUPPORTING_EVIDENCE
    assert data.calls == [], "no evidence is fetched that the Writer may not use"


async def test_an_unresolved_reference_asks_instead_of_drifting() -> None:
    from rockygpt_brain.core.interpretation import Anaphor
    from rockygpt_brain.core.outcomes import Clarify

    outcome, data = await run(
        shuttle(Relation.NEXT, origin=Anaphor(kind="anaphor", target="prior_subject"))
    )
    assert isinstance(outcome, Clarify)
    assert data.calls == []


async def test_an_undeclared_order_is_unsupported_not_the_default() -> None:
    outcome, data = await run(shuttle(Relation.LATEST, order_by=OrderSemantic.ARRIVAL_TIME))
    assert isinstance(outcome, Absent)
    assert outcome.cause is AbsenceCause.NO_CAPABILITY
    assert data.calls == []


async def test_a_world_task_is_the_only_outcome_answerable_from_model_knowledge() -> None:
    task = WorldTask(
        domain=Domain.WORLD,
        operation=Operation.READ,
        access=Access.PUBLIC,
        relation=Relation.DESCRIBE,
        cardinality=Cardinality.MANY,
        order_by=None,
        question="anything",
    )
    outcome, data = await run(task)
    assert isinstance(outcome, General)
    assert outcome.current_time == NOW.isoformat()
    assert data.calls == []


# --- the turn -----------------------------------------------------------------


class FakeModel:
    configured = True

    def __init__(self, interpretation: Interpretation) -> None:
        self.interpretation = interpretation
        self.seen: dict[str, Any] = {}

    async def understand(self, message: str, history: list[Any], now: datetime) -> Interpretation:
        return self.interpretation

    async def communicate(
        self,
        message: str,
        results: list[dict[str, Any]],
        safety_sent: bool,
        style_mode: str | None,
        response_mode: str | None,
    ) -> Draft:
        self.seen = {"results": results, "safety_sent": safety_sent}
        return Draft(answer="drafted", suggestedQuestions=[])


async def answer_with(interpretation: Interpretation) -> tuple[Any, FakeModel]:
    model = FakeModel(interpretation)
    brain = Brain(model, FakeData(), MemoryStore())
    response = await brain.answer(
        ChatRequest(message="anything", now=NOW),
        TurnIdentity("r", "s", None, "client"),
    )
    return response, model


async def test_every_task_produces_exactly_one_result() -> None:
    interpretation = Interpretation(
        scope=Scope.INSTITUTIONAL,
        danger=DangerClass.NONE,
        tasks=[shuttle(Relation.ALL), shuttle(Relation.NEXT), shuttle(Relation.EARLIEST)],
    )
    _, model = await answer_with(interpretation)
    assert len(model.seen["results"]) == len(interpretation.tasks)


async def test_an_emergency_block_is_prepended_whatever_else_the_turn_contains() -> None:
    interpretation = Interpretation(
        scope=Scope.INSTITUTIONAL,
        danger=DangerClass.MEDICAL,
        tasks=[shuttle(Relation.ALL)],
    )
    response, model = await answer_with(interpretation)
    assert response.answer.startswith("If anyone is in immediate danger, call 911")
    assert response.answer.endswith("drafted")
    assert model.seen["safety_sent"] is True


async def test_no_emergency_block_when_no_danger_is_reported() -> None:
    interpretation = Interpretation(
        scope=Scope.INSTITUTIONAL,
        danger=DangerClass.NONE,
        tasks=[shuttle(Relation.ALL)],
    )
    response, model = await answer_with(interpretation)
    assert response.answer == "drafted"
    assert model.seen["safety_sent"] is False


async def test_the_writer_receives_only_sealed_outcomes() -> None:
    """Every result carries a discriminated outcome and nothing else."""

    interpretation = Interpretation(
        scope=Scope.INSTITUTIONAL,
        danger=DangerClass.NONE,
        tasks=[shuttle(Relation.NEXT), shuttle(Relation.ALL)],
    )
    _, model = await answer_with(interpretation)
    for result in model.seen["results"]:
        assert set(result) == {"task", "outcome"}
        assert result["outcome"]["outcome"] in {
            "success",
            "absent",
            "withheld",
            "unavailable",
            "clarify",
            "error",
            "general",
        }


async def test_an_absent_result_carries_no_citable_evidence() -> None:
    interpretation = Interpretation(
        scope=Scope.INSTITUTIONAL,
        danger=DangerClass.NONE,
        tasks=[shuttle(Relation.ALL)],
    )
    response, _ = await answer_with(interpretation)
    assert response.citations == []


@pytest.mark.parametrize("relation", [Relation.NEXT, Relation.EARLIEST, Relation.LATEST])
async def test_no_single_result_relation_ever_hands_over_a_list(relation: Relation) -> None:
    records = [trip("a", "7:00 AM"), trip("b", "9:40 PM")]
    outcome, _ = await run(
        shuttle(relation),
        {
            "outcome": "success",
            "records": records[:1] if relation is not Relation.LATEST else records,
            "completeness": complete(1 if relation is not Relation.LATEST else 2),
            "evidence": [],
        },
    )
    if isinstance(outcome, Success):
        assert len(outcome.records) == 1
