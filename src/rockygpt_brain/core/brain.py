"""One request, start to finish.

    the question
        -> BRAIN #1  understand it — what is it actually asking?  (planner.py)
        -> BRAIN #2  plan it — what should be done about that?    (planner.py)
        -> PYTHON    run the lane the plan names, or fail         (execute.py)
        -> BRAIN #3  translate what came back into an answer      (model.py)

Three brains and a lane, in that order, and the trace carries one entry for
each. Each takes the one before it and turns it into something else: words into
an understanding, an understanding into a plan, a plan into rows, rows into
prose.

The order is the point. BRAIN #2 is given the resolved question and neither the
conversation nor the words as typed, so it cannot plan around a bad reading.
BRAIN #3 comes last because what the lane returned is what there is to write
about.

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
from rockygpt_brain.core.plan import Plan, Understanding
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
        memory: dict[str, Any] = {
            "currentTime": now.isoformat(),
            "earlierTurns": earlier,
        }
        if request.style_mode:
            memory["styleMode"] = request.style_mode
        if request.response_mode:
            memory["responseMode"] = request.response_mode

        # 2. BRAIN #1 reads the question, then BRAIN #2 plans from what it
        # read — and from nothing else. A brain that does not answer, or a plan
        # the registry will not accept, ends the turn: nothing downstream is
        # allowed to make up for it.
        read = await self._planner.understand(request.message, earlier, now.isoformat())
        drafted = await self._planner.plan(read.resolved, now.isoformat())
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

        # 4. BRAIN #3 — turn what the lane returned into an answer
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
            memory=memory,
            # What BRAIN #1 made of the question, and separately what it had
            # to borrow to get there. Split because the first is produced on
            # every turn and the second only when the conversation mattered.
            understanding={
                "normalizedQuestion": read.normalized,
                "usesContext": read.uses_context,
                "resolvedQuestion": read.resolved,
            },
            context=_context(read, earlier),
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


def _context(read: Understanding, earlier: list[dict[str, Any]]) -> dict[str, Any]:
    """How the question was worked out, and empty when it needed no working out.

    The gate is `usesContext`, which BRAIN #1 states outright. It is the only
    stage that can see the conversation, so it is the only one in a position to
    say whether this question needed it — and asking it beats every proxy tried
    here: a diff against the question caught typo corrections, and `references`
    flagged "you" and missed a subject left out altogether.

    `contextUsed` is looked up here from the positions BRAIN #1 named, so what
    is shown is what was actually said rather than its recollection of it.
    Positions that do not exist are dropped — a miscounted index is a wrong
    annotation, not a wrong answer, and should not cost the turn.
    """
    if not read.uses_context:
        return {}
    used = [earlier[i] for i in read.used_turns if 0 <= i < len(earlier)]
    return {
        "references": [r.model_dump(by_alias=True) for r in read.references],
        "contextUsed": used,
    }


class PlanRejected(Exception):
    """Why the registry would not accept a plan. The cause of the ServiceError."""
