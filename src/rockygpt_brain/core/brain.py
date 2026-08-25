"""One request, start to finish.

    question in -> one model call -> answer out

There is no router and no lane, because there is nothing to route between yet.
When a second way of answering exists, the choice goes in `answer`, between the
two halves below.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from rockygpt_brain.api.contracts import BrainTrace, ChatRequest, ChatSuccess
from rockygpt_brain.core.model import ModelPort
from rockygpt_brain.services.memory import MemoryStore


@dataclass(slots=True)
class TurnIdentity:
    request_id: str
    session_id: str
    visitor_id: str | None
    question_origin: Literal["client", "dev", "bot"]


class Brain:
    def __init__(
        self,
        model: ModelPort,
        memory: MemoryStore,
        timezone: str = "America/New_York",
    ) -> None:
        self._model = model
        self._memory = memory
        self._tz = ZoneInfo(timezone)

    async def answer(self, request: ChatRequest, identity: TurnIdentity) -> ChatSuccess:
        started = time.monotonic()
        now = (request.now or datetime.now(UTC)).astimezone(self._tz)
        history = self._memory.history(identity.session_id)
        context = [turn.model_dump() for turn in request.history] or history

        # IN
        question = {"question": request.message, "currentTime": now.isoformat()}

        draft = await self._model.answer(
            request.message,
            context,
            now.isoformat(),
            request.style_mode,
            request.response_mode,
        )

        # OUT
        result = {"answer": draft.answer}

        response = ChatSuccess(
            request_id=identity.request_id,
            answer=draft.answer,
            route="general",
            citations=[],
            ui_actions=[],
            suggested_questions=draft.suggested_questions[:10],
            brain_trace=BrainTrace(input=question, output=result),
        )
        self._memory.record(
            request_id=identity.request_id,
            session_id=identity.session_id,
            visitor_id=identity.visitor_id,
            question_origin=identity.question_origin,
            user_message=request.message,
            assistant_message=response.answer,
            route=response.route,
            tools=[],
            tool_arguments=question,
            citations=[],
            result=result,
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
        )
        return response
