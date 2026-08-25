"""What the brain does, end to end. One lane, so there is not much to check."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.api.contracts import ChatRequest
from rockygpt_brain.core.brain import Brain, TurnIdentity
from rockygpt_brain.core.intent import GeneralIntent, Intent, Lane
from rockygpt_brain.core.model import Draft
from rockygpt_brain.services.memory import MemoryStore

TZ = ZoneInfo("America/New_York")
NOW = datetime(2031, 3, 6, 18, 30, tzinfo=UTC)


class FakeModel:
    configured = True

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    async def understand(self, message: str, context: list[dict[str, Any]]) -> Intent:
        self.seen["context"] = context
        return Intent(decision=GeneralIntent(lane=Lane.GENERAL, question=message))

    async def communicate(
        self,
        message: str,
        intent: Intent,
        result: dict[str, Any],
        style_mode: str | None,
        response_mode: str | None,
    ) -> Draft:
        self.seen["result"] = result
        return Draft(answer="written", suggestedQuestions=["a", "b"])


async def answer(message: str = "anything") -> tuple[Any, FakeModel]:
    model = FakeModel()
    brain = Brain(model, MemoryStore())
    response = await brain.answer(
        ChatRequest(message=message, now=NOW), TurnIdentity("r", "s", None, "client")
    )
    return response, model


async def test_a_turn_runs_end_to_end() -> None:
    response, _ = await answer()
    assert response.route == "general"
    assert response.answer == "written"
    assert response.request_id == "r"


async def test_the_result_carries_campus_local_time() -> None:
    _, model = await answer()
    assert model.seen["result"]["currentTime"] == NOW.astimezone(TZ).isoformat()
    assert model.seen["result"]["outcome"] == "general"


async def test_nothing_is_cited_because_nothing_is_looked_up() -> None:
    response, _ = await answer()
    assert response.citations == []
    assert response.ui_actions == []


async def test_the_trace_shows_what_each_side_saw() -> None:
    response, _ = await answer("a question")
    assert response.brain_trace.input["question"] == "a question"
    assert response.brain_trace.output["outcome"] == "general"


async def test_the_turn_is_recorded_for_the_next_one() -> None:
    model = FakeModel()
    brain = Brain(model, MemoryStore())
    identity = TurnIdentity("r1", "s1", None, "client")
    await brain.answer(ChatRequest(message="first", now=NOW), identity)
    await brain.answer(
        ChatRequest(message="second", now=NOW), TurnIdentity("r2", "s1", None, "client")
    )
    assert model.seen["context"], "the second turn sees the first"
