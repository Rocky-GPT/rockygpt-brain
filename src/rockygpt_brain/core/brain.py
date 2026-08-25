"""One request, start to finish.

    the question
        -> BRAIN #1  understand it, and write a plan   (planner.py + validate.py)
        -> PYTHON    run the lane the plan names       (execute.py)
        -> BRAIN #2  write the answer                  (model.py)

Four stages, in that order, and the trace carries one entry for each. The order
is the point: BRAIN #2 answers after the lane has run, because once a lane has
an executor its results are what there is to write about.

No stage compensates for the one before it. A planner that does not answer, a
plan the registry rejects, a lookup that fails — each ends the turn rather than
letting the stage after it paper over the gap. The alternative is an answer
written around a lookup that never happened, which reads exactly like one that
did, and only the trace would ever know the difference.

The question stage holds the words the student typed and nothing else.
Everything the turn is read against — the clock, the earlier turns, the modes
the client asked for — is the context stage beside it, which leaves the plan
stage as purely what BRAIN #1 decided.
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
from rockygpt_brain.services.web import WebPort


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
        web: WebPort,
        memory: MemoryStore,
        timezone: str = "America/New_York",
    ) -> None:
        self._model = model
        self._planner = planner
        self._data = data
        self._web = web
        self._memory = memory
        self._tz = ZoneInfo(timezone)

    async def answer(self, request: ChatRequest, identity: TurnIdentity) -> ChatSuccess:
        started = time.monotonic()
        now = (request.now or datetime.now(UTC)).astimezone(self._tz)
        # The client is the authority on its own conversation when it says
        # anything at all. `[]` means there is nothing earlier; only a missing
        # field falls back to what this process remembers of the session.
        earlier = (
            [turn.model_dump() for turn in request.history]
            if request.history is not None
            else self._memory.history(identity.session_id)
        )

        # 1. the question in the student's own words, and separately everything
        # else the turn is read against. The clock is context too: Python sets
        # it, both brains are handed it, and it is on every turn — including
        # ones with nothing temporal in them, because a trace that hid a real
        # input would cost an hour the first time a date came out wrong. Do not
        # make it conditional.
        question = {"question": request.message}
        context: dict[str, Any] = {
            "currentTime": now.isoformat(),
            "earlierTurns": earlier,
        }
        if request.style_mode:
            context["styleMode"] = request.style_mode
        if request.response_mode:
            context["responseMode"] = request.response_mode

        # 2. BRAIN #1 — understand it, and write a plan. A planner that does
        # not answer, or a plan the registry will not accept, ends the turn:
        # nothing downstream is allowed to make up for it.
        drafted = await self._planner.plan(request.message, earlier, now.isoformat())
        checked = check(drafted, now)
        if isinstance(checked, Rejected):
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "Rocky could not work out how to answer that.",
                retryable=True,
            ) from PlanRejected(checked.reason)

        # 3. PYTHON — run the lane. Raises if it cannot.
        execution = await run(checked, now, self._data, self._web)

        # 4. BRAIN #2 — write the answer, from what the lane returned
        draft = await self._model.answer(
            request.message,
            earlier,
            now.isoformat(),
            request.style_mode,
            request.response_mode,
            execution.grounding(),
        )

        trace = BrainTrace(
            question=question,
            context=context,
            plan=checked.summary(),
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


class PlanRejected(Exception):
    """Why the registry would not accept a plan. The cause of the ServiceError."""
