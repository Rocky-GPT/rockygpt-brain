"""One request, start to finish.

    question in -> plan it, answer it -> answer out

Two model calls, made at the same time because neither waits on the other.
AI #1 translates the question into a plan; the answer model writes the prose.
The plan is checked before Rocky would act on it, and is recorded on every turn
so a wrong plan is visible in the log rather than only in a wrong answer.

The plan belongs to IN. It is what Rocky understood the question to be, and
what an executor will act on; OUT is what came back from acting on it.

Nothing executes a plan yet. When a lane grows an executor, it goes in `answer`
where `checked` is available and before the answer is composed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from rockygpt_brain.api.contracts import BrainTrace, ChatRequest, ChatSuccess
from rockygpt_brain.core.model import ModelPort
from rockygpt_brain.core.plan import Plan
from rockygpt_brain.core.planner import PlannerPort
from rockygpt_brain.core.validate import Rejected, check
from rockygpt_brain.errors import ServiceError
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
        planner: PlannerPort,
        memory: MemoryStore,
        timezone: str = "America/New_York",
    ) -> None:
        self._model = model
        self._planner = planner
        self._memory = memory
        self._tz = ZoneInfo(timezone)

    async def answer(self, request: ChatRequest, identity: TurnIdentity) -> ChatSuccess:
        started = time.monotonic()
        now = (request.now or datetime.now(UTC)).astimezone(self._tz)
        history = self._memory.history(identity.session_id)
        context = [turn.model_dump() for turn in request.history] or history

        checked, draft = await asyncio.gather(
            self._plan(request.message, context, now),
            self._model.answer(
                request.message,
                context,
                now.isoformat(),
                request.style_mode,
                request.response_mode,
            ),
        )

        # IN — the question, and what AI #1 made of it
        question = {
            "question": request.message,
            "currentTime": now.isoformat(),
            "plan": _traced(checked),
        }

        # OUT
        result = {"answer": draft.answer}

        response = ChatSuccess(
            request_id=identity.request_id,
            answer=draft.answer,
            route=checked.lane.value.lower() if isinstance(checked, Plan) else "general",
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

    async def _plan(
        self,
        message: str,
        context: list[dict[str, Any]],
        now: datetime,
    ) -> Plan | Rejected:
        """AI #1, then the check. A planner outage costs the plan, not the answer."""
        try:
            drafted = await self._planner.plan(message, context, now.isoformat())
        except ServiceError:
            return Rejected("the planner was unavailable")
        return check(drafted, now)


def _traced(checked: Plan | Rejected) -> dict[str, Any]:
    return checked.summary() if isinstance(checked, Plan) else {"rejected": checked.reason}
