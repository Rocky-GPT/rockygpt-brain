"""A question goes in, a plan and an answer come out."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.api.contracts import (
    HISTORY_EXCHANGES,
    ChatRequest,
    ChatSuccess,
    ChatTurn,
)
from rockygpt_brain.core.brain import Brain, TurnIdentity
from rockygpt_brain.core.model import Draft
from rockygpt_brain.core.plan import Filter, Lane, Operation, Plan
from rockygpt_brain.errors import ServiceError
from rockygpt_brain.services.memory import MemoryStore

TZ = ZoneInfo("America/New_York")
NOW = datetime(2031, 3, 6, 18, 30, tzinfo=UTC)
CLOCK = NOW.astimezone(TZ).isoformat()


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
        grounding: dict[str, Any],
    ) -> Draft:
        self.seen = {
            "question": question,
            "context": context,
            "currentTime": current_time,
            "grounding": grounding,
        }
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


class FakeData:
    """No capability is asked for in these tests, so nothing is looked up."""

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return []


async def ask(
    message: str = "anything",
    memory: MemoryStore | None = None,
    rid: str = "r",
    planner: FakePlanner | None = None,
) -> tuple[ChatSuccess, FakeModel]:
    model = FakeModel()
    brain = Brain(model, planner or FakePlanner(), FakeData(), memory or MemoryStore())
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
    response, model = await ask("second", memory, "r2")
    assert model.seen["context"], "the second turn sees the first"
    assert response.brain_trace.context["earlierTurns"] == model.seen["context"], (
        "what the trace shows is what the model was given"
    )


async def test_an_empty_history_is_taken_at_its_word() -> None:
    """A client that says the conversation is empty is not overruled."""
    memory = MemoryStore()
    await ask("first", memory, "r1")

    model = FakeModel()
    brain = Brain(model, FakePlanner(), FakeData(), memory)
    response = await brain.answer(
        ChatRequest(message="second", history=[], now=NOW),
        TurnIdentity("r2", "s", None, "client"),
    )
    assert model.seen["context"] == [], "the earlier turn is not resurrected"
    assert response.brain_trace.context["earlierTurns"] == []


async def test_a_client_that_sends_no_history_still_gets_the_sessions() -> None:
    """Omitting the field means "I do not track this", so the brain fills in."""
    memory = MemoryStore()
    await ask("first", memory, "r1")
    _, model = await ask("second", memory, "r2")
    assert model.seen["context"], "a client with no history of its own gets ours"


async def test_both_paths_see_the_same_distance_back() -> None:
    """A client that sends history and one that omits it get the same depth."""
    memory = MemoryStore()
    for turn in range(HISTORY_EXCHANGES + 4):
        await ask(f"q{turn}", memory, f"r{turn}")

    _, model = await ask("follow up", memory, "last")
    assert len(model.seen["context"]) == HISTORY_EXCHANGES, "the memory fallback"

    sent = [ChatTurn(role="user", content="x")] * (HISTORY_EXCHANGES * 2)
    ChatRequest(message="m", history=sent)  # the contract accepts the same depth


async def test_the_modes_the_ui_asked_for_are_on_the_turn() -> None:
    model = FakeModel()
    brain = Brain(model, FakePlanner(), FakeData(), MemoryStore())
    response = await brain.answer(
        ChatRequest(message="m", now=NOW, style_mode="warm", response_mode="concise"),
        TurnIdentity("r", "s", None, "client"),
    )
    assert response.brain_trace.context["styleMode"] == "warm"
    assert response.brain_trace.context["responseMode"] == "concise"


# The planning half


async def test_the_planner_is_told_the_same_question_and_time() -> None:
    planner = FakePlanner()
    await ask("a question", planner=planner)
    assert planner.seen["question"] == "a question"
    assert planner.seen["currentTime"] == NOW.astimezone(TZ).isoformat()


async def test_the_plan_is_its_own_stage() -> None:
    plan = Plan(
        lane=Lane.CODE,
        capability="shuttle",
        filters=[Filter(field="date", value="today")],
        operation=Operation(order_by="departureTime", direction="descending", limit=1),
    )
    response, _ = await ask(planner=FakePlanner(plan))
    assert response.brain_trace.plan == {
        "lane": "CODE",
        "capability": "shuttle",
        "filters": {"date": "2031-03-06"},
        "operation": {"orderBy": "departureTime", "direction": "descending", "limit": 1},
    }


async def test_the_turn_reads_end_to_end_as_four_stages() -> None:
    response, _ = await ask("a question")
    trace = response.brain_trace
    assert trace.question == {"question": "a question"}, "the words, and nothing else"
    assert trace.context == {"currentTime": CLOCK, "earlierTurns": []}
    assert trace.plan == {"lane": "GENERAL"}, "the plan alone — the clock is context"
    assert trace.execution == {
        "answerFrom": "ownKnowledge",
        "note": "nothing to look up; answered from what the model knows",
    }
    assert trace.answer == {"answer": "written"}


async def test_brain_two_is_grounded_on_every_lane() -> None:
    """Every turn hands BRAIN #2 an instruction, even when nothing was looked up."""
    _, model = await ask()
    assert model.seen["grounding"] == {"answerFrom": "ownKnowledge"}


async def test_brain_two_is_never_told_a_lookup_failed() -> None:
    """It would apologise for the capability instead of answering the question."""
    plan = Plan(lane=Lane.CODE, capability="menu")
    _, model = await ask(planner=FakePlanner(plan))
    assert model.seen["grounding"] == {"answerFrom": "ownKnowledge"}, (
        "a missing executor looks exactly like a question that needed no lookup"
    )


async def test_a_stage_that_did_not_run_carries_no_results() -> None:
    """A turn answered from the model's own knowledge must not look looked-up."""
    response, _ = await ask(planner=FakePlanner(Plan(lane=Lane.RAG, topic="parking")))
    assert "results" not in response.brain_trace.execution
    assert "RAG" in response.brain_trace.execution["note"]


async def test_the_route_is_the_lane() -> None:
    response, _ = await ask(planner=FakePlanner(Plan(lane=Lane.RAG, topic="parking")))
    assert response.route == "rag"


async def test_a_rejected_plan_says_why_and_still_answers() -> None:
    response, _ = await ask(planner=FakePlanner(Plan(lane=Lane.CODE, capability="weather")))
    assert response.answer == "written"
    assert response.route == "general"
    assert "weather" in response.brain_trace.plan["rejected"]
    assert "weather" in response.brain_trace.execution["note"]


async def test_a_planner_outage_costs_the_plan_not_the_answer() -> None:
    response, _ = await ask(planner=FakePlanner(fails=True))
    assert response.answer == "written"
    assert response.brain_trace.plan == {"rejected": "the planner was unavailable"}
