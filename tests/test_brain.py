"""A question goes in, an answer comes out."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.api.contracts import ChatRequest
from rockygpt_brain.core.brain import Brain, TurnIdentity
from rockygpt_brain.core.model import Draft
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
        return Draft(answer="written", suggestedQuestions=["a"])


async def ask(message: str = "anything", memory: MemoryStore | None = None, rid: str = "r"):
    model = FakeModel()
    brain = Brain(model, memory or MemoryStore())
    response = await brain.answer(
        ChatRequest(message=message, now=NOW), TurnIdentity(rid, "s", None, "client")
    )
    return response, model


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


async def test_the_trace_is_the_question_in_and_the_answer_out() -> None:
    response, _ = await ask("a question")
    assert response.brain_trace.input["question"] == "a question"
    assert response.brain_trace.output == {"answer": "written"}


async def test_a_follow_up_sees_the_earlier_turn() -> None:
    memory = MemoryStore()
    await ask("first", memory, "r1")
    _, model = await ask("second", memory, "r2")
    assert model.seen["context"], "the second turn sees the first"
