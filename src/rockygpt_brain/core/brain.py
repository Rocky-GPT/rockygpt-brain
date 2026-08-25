"""One request, start to finish.

    the question
        -> BRAIN #1  understand it, and write a plan   (planner.py + validate.py)
        -> PYTHON    run the lane the plan names       (execute.py)
        -> BRAIN #2  write the answer                  (model.py)

Four stages, in that order, and the trace carries one entry for each. The order
is the point: BRAIN #2 answers after the lane has run, because once a lane has
an executor its results are what there is to write about.

Nothing has an executor yet, so today the middle stage only records which lane
would have run. When one lands, its results go to BRAIN #2 from here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from rockygpt_brain.api.contracts import BrainTrace, ChatRequest, ChatSuccess
from rockygpt_brain.core.execute import run
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

        # 1. the question
        question = {"question": request.message, "currentTime": now.isoformat()}

        # 2. BRAIN #1 — understand it, and write a plan
        checked = await self._plan(request.message, context, now)

        # 3. PYTHON — run the lane
        execution = run(checked)

        # 4. BRAIN #2 — write the answer
        draft = await self._model.answer(
            request.message,
            context,
            now.isoformat(),
            request.style_mode,
            request.response_mode,
        )

        trace = BrainTrace(
            question=question,
            plan=_traced(checked),
            execution=execution.summary(),
            answer={"answer": draft.answer},
        )
        response = ChatSuccess(
            request_id=identity.request_id,
            answer=draft.answer,
            route=checked.lane.value.lower() if isinstance(checked, Plan) else "general",
            citations=[],
            ui_actions=[],
            suggested_questions=draft.suggested_questions[:10],
            brain_trace=trace,
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
            tool_arguments=trace.plan,
            citations=[],
            result={"execution": trace.execution, "answer": trace.answer},
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
        )
        return response

    async def _plan(
        self,
        message: str,
        context: list[dict[str, Any]],
        now: datetime,
    ) -> Plan | Rejected:
        """BRAIN #1, then the check. A planner outage costs the plan, not the answer."""
        try:
            drafted = await self._planner.plan(message, context, now.isoformat())
        except ServiceError:
            return Rejected("the planner was unavailable")
        return check(drafted, now)


def _traced(checked: Plan | Rejected) -> dict[str, Any]:
    return checked.summary() if isinstance(checked, Plan) else {"rejected": checked.reason}
