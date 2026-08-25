"""A question goes in, a plan and an answer come out."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.api.contracts import ChatRequest, ChatSuccess
from rockygpt_brain.core.brain import Brain, TurnIdentity
from rockygpt_brain.core.model import Draft
from rockygpt_brain.core.plan import Filter, Lane, Operation, Plan
from rockygpt_brain.errors import ServiceError
from rockygpt_brain.services.memory import MemoryStore

TZ = ZoneInfo("America/New_York")
NOW = datetime(2031, 3, 6, 18, 30, tzinfo=UTC)


class FakeModel:
    configured = True

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    async def answer(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
        style_mode: str | None,
        response_mode: str | None,
    ) -> Draft:
        self.seen = {"question": question, "context": context, "currentTime": current_time}
        return Draft(answer="written", suggested_questions=["a"])


class FakePlanner:
    configured = True

    def __init__(self, plan: Plan | None = None, fails: bool = False) -> None:
        self._plan = plan or Plan(lane=Lane.GENERAL)
        self._fails = fails
        self.seen: dict[str, Any] = {}

    async def plan(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Plan:
        self.seen = {"question": question, "context": context, "currentTime": current_time}
        if self._fails:
            raise ServiceError(503, "SERVICE_UNAVAILABLE", "down", retryable=True)
        return self._plan


async def ask(
    message: str = "anything",
    memory: MemoryStore | None = None,
    rid: str = "r",
    planner: FakePlanner | None = None,
) -> tuple[ChatSuccess, FakeModel]:
    model = FakeModel()
    brain = Brain(model, planner or FakePlanner(), memory or MemoryStore())
    response = await brain.answer(
        ChatRequest(message=message, now=NOW), TurnIdentity(rid, "s", None, "client")
    )
    return response, model


# The answer half, unchanged


async def test_the_question_reaches_the_model_unchanged() -> None:
    _, model = await ask("a question")
    assert model.seen["question"] == "a question"


async def test_the_answer_comes_back_unchanged() -> None:
    response, _ = await ask()
    assert response.answer == "written"


async def test_the_model_is_told_the_local_time() -> None:
    _, model = await ask()
    assert model.seen["currentTime"] == NOW.astimezone(TZ).isoformat()


async def test_nothing_is_cited_because_nothing_is_looked_up() -> None:
    response, _ = await ask()
    assert response.citations == []
    assert response.ui_actions == []


async def test_a_follow_up_sees_the_earlier_turn() -> None:
    memory = MemoryStore()
    await ask("first", memory, "r1")
    _, model = await ask("second", memory, "r2")
    assert model.seen["context"], "the second turn sees the first"


# The planning half


async def test_the_planner_is_told_the_same_question_and_time() -> None:
    planner = FakePlanner()
    await ask("a question", planner=planner)
    assert planner.seen["question"] == "a question"
    assert planner.seen["currentTime"] == NOW.astimezone(TZ).isoformat()


async def test_the_checked_plan_is_what_rocky_was_given() -> None:
    plan = Plan(
        lane=Lane.CODE,
        capability="shuttle",
        filters=[Filter(field="date", value="today")],
        operation=Operation(order_by="departureTime", direction="descending", limit=1),
    )
    response, _ = await ask(planner=FakePlanner(plan))
    assert response.brain_trace.input["plan"] == {
        "lane": "CODE",
        "capability": "shuttle",
        "filters": {"date": "2031-03-06"},
        "operation": {"orderBy": "departureTime", "direction": "descending", "limit": 1},
    }


async def test_the_answer_is_all_that_comes_out() -> None:
    """Nothing executes a plan yet, so OUT is the answer and nothing else."""
    response, _ = await ask()
    assert response.brain_trace.output == {"answer": "written"}


async def test_the_route_is_the_lane() -> None:
    response, _ = await ask(planner=FakePlanner(Plan(lane=Lane.RAG, topic="parking")))
    assert response.route == "rag"


async def test_a_rejected_plan_says_why_and_still_answers() -> None:
    response, _ = await ask(planner=FakePlanner(Plan(lane=Lane.CODE, capability="weather")))
    assert response.answer == "written"
    assert response.route == "general"
    assert "weather" in response.brain_trace.input["plan"]["rejected"]


async def test_a_planner_outage_costs_the_plan_not_the_answer() -> None:
    response, _ = await ask(planner=FakePlanner(fails=True))
    assert response.answer == "written"
    assert response.brain_trace.input["plan"] == {"rejected": "the planner was unavailable"}
