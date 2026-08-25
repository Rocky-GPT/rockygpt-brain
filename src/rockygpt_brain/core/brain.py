"""One request, start to finish.

    the question
        -> BRAIN #1  understand it, and write a plan   (planner.py + validate.py)
        -> PYTHON    run the lane the plan names       (execute.py)
        -> BRAIN #2  write the answer                  (model.py)

Four stages, in that order, and the trace carries one entry for each. The order
is the point: BRAIN #2 answers after the lane has run, because once a lane has
an executor its results are what there is to write about.

A lane without an executor records that it did not run, and BRAIN #2 answers
from its own knowledge. A lane with one hands its results to BRAIN #2, which is
the whole reason the stages are in this order.

The question stage holds what arrived with the request — the words, the earlier
turns, and the modes the UI asked for. The clock is not part of it: the browser
never sends a time and the proxy would drop one, so it sits with BRAIN #1,
which is the stage that read the question against it.
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
from rockygpt_brain.services.data import DataPort
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
        data: DataPort,
        memory: MemoryStore,
        timezone: str = "America/New_York",
    ) -> None:
        self._model = model
        self._planner = planner
        self._data = data
        self._memory = memory
        self._tz = ZoneInfo(timezone)

    async def answer(self, request: ChatRequest, identity: TurnIdentity) -> ChatSuccess:
        started = time.monotonic()
        now = (request.now or datetime.now(UTC)).astimezone(self._tz)
        history = self._memory.history(identity.session_id)
        context = [turn.model_dump() for turn in request.history] or history

        # 1. the question, and everything that arrived with it. The clock is
        # not here — Python sets that, and it belongs to the stage that read
        # the question against it.
        question: dict[str, Any] = {
            "question": request.message,
            "earlierTurns": context,
        }
        if request.style_mode:
            question["styleMode"] = request.style_mode
        if request.response_mode:
            question["responseMode"] = request.response_mode

        # 2. BRAIN #1 — understand it, and write a plan
        checked = await self._plan(request.message, context, now)

        # 3. PYTHON — run the lane
        execution = await run(checked, now, self._data)

        # 4. BRAIN #2 — write the answer, from what the lane returned
        draft = await self._model.answer(
            request.message,
            context,
            now.isoformat(),
            request.style_mode,
            request.response_mode,
            execution.grounding(),
        )

        trace = BrainTrace(
            question=question,
            # The clock leads the plan because it is what the question was read
            # against: `today` means nothing until an instant fixes it. It is
            # shown on every turn, including turns with nothing temporal in
            # them — both brains are handed it unconditionally, and a trace
            # that hid a real input would cost an hour the first time a date
            # came out wrong. Do not make it conditional.
            plan={"currentTime": now.isoformat(), **_traced(checked)},
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
