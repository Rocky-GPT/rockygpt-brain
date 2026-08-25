"""A question goes in, a plan and an answer come out."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.api.contracts import (
    HISTORY_EXCHANGES,
    ChatRequest,
    ChatSuccess,
    ChatTurn,
)
from rockygpt_brain.core.brain import Brain, TurnIdentity
from rockygpt_brain.core.model import Draft
from rockygpt_brain.core.plan import (
    Filter,
    Lane,
    Operation,
    Plan,
    Reference,
    Understanding,
)
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

    def __init__(
        self,
        plan: Plan | None = None,
        fails: bool = False,
        read: Understanding | None = None,
    ) -> None:
        self._plan = plan or Plan(lane=Lane.GENERAL)
        self._read = read or Understanding(normalized="q", resolved="q")
        self._fails = fails
        self.seen: dict[str, Any] = {}
        self.planned_from: str | None = None

    async def understand(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Understanding:
        self.seen = {"question": question, "context": context, "currentTime": current_time}
        if self._fails:
            raise ServiceError(503, "SERVICE_UNAVAILABLE", "down", retryable=True)
        return self._read

    async def plan(self, resolved: str, current_time: str) -> Plan:
        self.planned_from = resolved
        return self._plan


class FakeData:
    """No capability is asked for in these tests, so nothing is looked up."""

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return []


class FakeWeb:
    configured = True

    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self.results = results or []
        self.searched: str | None = None

    async def search(self, query: str) -> list[dict[str, Any]]:
        self.searched = query
        return self.results


async def ask(
    message: str = "anything",
    memory: MemoryStore | None = None,
    rid: str = "r",
    planner: FakePlanner | None = None,
) -> tuple[ChatSuccess, FakeModel]:
    model = FakeModel()
    brain = Brain(model, planner or FakePlanner(), FakeData(), FakeWeb(), memory or MemoryStore())
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
    assert response.brain_trace.memory["earlierTurns"] == model.seen["context"], (
        "what the trace shows is what the model was given"
    )


async def test_a_question_that_stands_alone_has_no_context_stage() -> None:
    """Nothing was resolved, so `normalized` and `resolved` match."""
    plan = Plan(
        lane=Lane.GENERAL,
        freshness="stable",
    )
    response, _ = await ask("what is the capital of France", planner=FakePlanner(plan))
    assert response.brain_trace.context == {}


async def test_a_reworded_question_is_not_a_borrowed_one() -> None:
    """BRAIN #1 said the question needed no conversation, so there is no stage."""
    plan = Plan(
        lane=Lane.GENERAL,
        freshness="stable",
    )
    response, _ = await ask("are you srueeee?", planner=FakePlanner(plan))
    assert response.brain_trace.context == {}, "a corrected spelling is not context"


async def test_the_context_stage_breaks_down_how_the_question_was_read() -> None:
    read = Understanding(
        normalized="population of it",
        references=[Reference(text="it", refers_to="Paris")],
        used_turns=[0],
        uses_context=True,
        resolved="population of Paris",
    )
    memory = MemoryStore()
    await ask("Capital of france", memory, "r1")
    planner = FakePlanner(Plan(lane=Lane.GENERAL, freshness="stable"), read=read)
    response, _ = await ask("population of it", memory, "r2", planner=planner)
    context = response.brain_trace.context
    assert context["normalizedQuestion"] == "population of it"
    assert context["references"] == [{"text": "it", "refersTo": "Paris"}]
    assert context["resolvedQuestion"] == "population of Paris"
    assert len(context["contextUsed"]) == 1, "looked up from the turn it named"
    assert planner.planned_from == "population of Paris", (
        "the plan is built from the resolved question, never the words typed"
    )


async def test_a_turn_position_that_does_not_exist_is_dropped() -> None:
    """A miscounted index is a wrong annotation, not a wrong answer."""
    read = Understanding(
        normalized="a",
        references=[Reference(text="it", refers_to="something")],
        used_turns=[7],
        uses_context=True,
        resolved="b",
    )
    response, _ = await ask("a", planner=FakePlanner(read=read))
    assert response.brain_trace.context["contextUsed"] == []


async def test_an_empty_history_is_taken_at_its_word() -> None:
    """A client that says the conversation is empty is not overruled."""
    memory = MemoryStore()
    await ask("first", memory, "r1")

    model = FakeModel()
    brain = Brain(model, FakePlanner(), FakeData(), FakeWeb(), memory)
    response = await brain.answer(
        ChatRequest(message="second", history=[], now=NOW),
        TurnIdentity("r2", "s", None, "client"),
    )
    assert model.seen["context"] == [], "the earlier turn is not resurrected"
    assert response.brain_trace.memory["earlierTurns"] == []


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
    brain = Brain(model, FakePlanner(), FakeData(), FakeWeb(), MemoryStore())
    response = await brain.answer(
        ChatRequest(message="m", now=NOW, style_mode="warm", response_mode="concise"),
        TurnIdentity("r", "s", None, "client"),
    )
    assert response.brain_trace.memory["styleMode"] == "warm"
    assert response.brain_trace.memory["responseMode"] == "concise"


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
    assert trace.memory == {"currentTime": CLOCK, "earlierTurns": []}
    assert trace.plan == {"lane": "GENERAL", "freshness": "stable"}
    assert trace.context == {}, "nothing was resolved, so there is no stage"
    assert trace.execution == {
        "answerFrom": "ownKnowledge",
        "note": "stable; answered from what the model knows",
    }
    assert trace.answer == {"answer": "written"}


async def test_brain_two_is_grounded_on_every_lane() -> None:
    """Every turn hands BRAIN #3 an instruction, even when nothing was looked up."""
    _, model = await ask()
    assert model.seen["grounding"] == {"answerFrom": "ownKnowledge"}


async def test_brain_two_never_runs_on_a_failed_lookup() -> None:
    """No stage compensates for the one before it, so there is nothing to write."""
    model = FakeModel()
    brain = Brain(
        model,
        FakePlanner(Plan(lane=Lane.CODE, capability="menu")),
        FakeData(),
        FakeWeb(),
        MemoryStore(),
    )
    with pytest.raises(ServiceError):
        await brain.answer(
            ChatRequest(message="m", now=NOW), TurnIdentity("r", "s", None, "client")
        )
    assert model.seen == {}, "BRAIN #3 was never called"


async def test_a_lane_with_no_executor_ends_the_turn() -> None:
    with pytest.raises(ServiceError) as raised:
        await ask(planner=FakePlanner(Plan(lane=Lane.RAG, topic="parking")))
    assert raised.value.status_code == 503


async def test_the_route_is_the_lane() -> None:
    plan = Plan(lane=Lane.CODE, capability="shuttle", operation=Operation(limit=1))
    response, _ = await ask(planner=FakePlanner(plan))
    assert response.route == "code"


async def test_a_rejected_plan_ends_the_turn() -> None:
    """The registry refusing a plan is a failure, not a cue to answer anyway."""
    with pytest.raises(ServiceError) as raised:
        await ask(planner=FakePlanner(Plan(lane=Lane.CODE, capability="weather")))
    assert "weather" in str(raised.value.__cause__)


async def test_a_planner_outage_ends_the_turn() -> None:
    """Everything downstream is read against the plan, so there is nothing to run."""
    with pytest.raises(ServiceError):
        await ask(planner=FakePlanner(fails=True))
