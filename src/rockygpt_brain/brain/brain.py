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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from rockygpt_brain.api.contracts import BrainTrace, ChatRequest, ChatSuccess, Citation
from rockygpt_brain.brain.execute.run import run
from rockygpt_brain.brain.execute.schema import (
    DOCUMENTS,
    INSUFFICIENT_EVIDENCE,
    WEB,
    Execution,
)
from rockygpt_brain.brain.plan.run import PlanPort
from rockygpt_brain.brain.plan.validate import Rejected, check
from rockygpt_brain.brain.understand.run import UnderstandPort
from rockygpt_brain.brain.understand.schema import Understanding
from rockygpt_brain.brain.understand.validate import ResolutionFailed, unresolved
from rockygpt_brain.brain.write.run import WritePort
from rockygpt_brain.context.memory import MemoryStore
from rockygpt_brain.errors import ServiceError, Unavailable
from rockygpt_brain.services.data import DataPort
from rockygpt_brain.services.rag.client import RagPort
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
        model: WritePort,
        understand: UnderstandPort,
        planner: PlanPort,
        data: DataPort,
        web: WebPort,
        documents: RagPort,
        memory: MemoryStore,
        timezone: str = "America/New_York",
    ) -> None:
        self._model = model
        self._understand = understand
        self._planner = planner
        self._data = data
        self._web = web
        self._documents = documents
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

        # Written in the `finally` below however this ends. Every stage adds
        # to it as it completes, so a turn that fails halfway is recorded with
        # exactly what it got through.
        recording = _Recording(identity, request.message, started)
        try:
            return await self._turn(request, identity, now, earlier, question, memory, recording)
        except ServiceError as exc:
            recording.failed = str(exc.__cause__ or exc)
            raise
        except Exception as exc:  # noqa: BLE001 — recorded, then re-raised untouched
            recording.failed = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            recording.write(self._memory)

    async def _turn(
        self,
        request: ChatRequest,
        identity: TurnIdentity,
        now: datetime,
        earlier: list[dict[str, Any]],
        question: dict[str, Any],
        memory: dict[str, Any],
        recording: _Recording,
    ) -> ChatSuccess:
        """The four stages. Raises rather than degrading, and is recorded either way."""
        # BRAIN #1 reads the question, then BRAIN #2 plans from what it read —
        # and from nothing else. A brain that does not answer, or a plan the
        # registry will not accept, ends the turn: nothing downstream is
        # allowed to make up for it.
        read = await self._understand.understand(request.message, earlier, now.isoformat())
        if failure := unresolved(read):
            raise Unavailable("Rocky could not work out what that was asking.") from (
                ResolutionFailed(failure)
            )
        drafted = await self._planner.plan(read.resolved, now.isoformat())
        checked = check(drafted, now)
        if isinstance(checked, Rejected):
            raise Unavailable("Rocky could not work out how to answer that.") from PlanRejected(
                checked.reason
            )

        recording.plan = checked.summary()
        recording.route = checked.lane.value.lower()

        # PYTHON — run the lane. Raises if it cannot.
        execution = await run(checked, now, self._data, self._web, self._documents)
        recording.execution = execution.summary()

        # BRAIN #3 — turn what the lane returned into an answer
        draft = await self._model.answer(
            request.message,
            earlier,
            now.isoformat(),
            request.style_mode,
            request.response_mode,
            execution.grounding(),
        )

        # The documents were searched and did not answer the question. That is
        # an outcome, not a failure: retrieval ran and worked. What changes is
        # who writes the last sentence — BRAIN #3 has just said its evidence was
        # thin, and a model in that position writes a hedged answer rather than
        # none, which reads exactly like a sourced one.
        #
        # Only the documents lane for now. `campusData` and `web` could be held
        # to the same rule, but each needs its own measurement first.
        unsupported = execution.answer_from == DOCUMENTS and not draft.sufficient_evidence
        answer = INSUFFICIENT_EVIDENCE if unsupported else draft.answer

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
            answer={"answer": answer, "sufficientEvidence": draft.sufficient_evidence},
        )
        # Nothing was answered from them, so nothing is cited. A citation on
        # an abstention points a reader at a page that does not say it.
        citations = [] if unsupported else _citations(execution, now)
        response = ChatSuccess(
            request_id=identity.request_id,
            answer=answer,
            route=recording.route,
            citations=citations,
            ui_actions=[],
            suggested_questions=draft.suggested_questions[:10],
            brain_trace=trace,
        )
        recording.answer = response.answer
        recording.citations = citations
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


def _citations(execution: Execution, now: datetime) -> list[Citation]:
    """The pages an answer came from, so a reader can check it.

    The web and the documents both produce these; campus rows are Rocky's own
    records and carry no page to point at, and a lane that looked nothing up
    has nothing to cite.

    A document passage brings its own title, because the retrieval service
    knows what page it cut the passage out of. A web result does not, so its
    title is the host, which is the part a reader recognises anyway — `insee.fr` says more about
    whether to trust a number than a headline would. That also makes the title
    a function of the URL, so the client deduplicating on `title|url` and this
    deduplicating on the URL agree rather than disagreeing quietly.

    A row Rocky cannot turn into a citation is dropped, never raised on. The
    answer is already written by this point, and losing it over a malformed URL
    would be the citation costing more than it is worth.
    """
    if execution.answer_from not in (WEB, DOCUMENTS):
        return []
    out: list[Citation] = []
    seen: set[str] = set()
    for row in execution.results:
        url = str(row.get("source") or row.get("url") or "")
        if url in seen:
            continue
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if not host:
            continue
        try:
            out.append(
                Citation(
                    title=str(row.get("title") or "") or host,
                    url=url,
                    snippet=str(row.get("fact") or row.get("passage") or "")[:1000] or None,
                    collected_at=now,
                )
            )
        except ValidationError:
            continue
        seen.add(url)
    return out


@dataclass(slots=True)
class _Recording:
    """What is known about this turn so far, written however it ends.

    The old code recorded at the end of the success path, so every turn that
    raised — a resolution that failed, a plan the registry refused, a lookup
    that could not run — left no trace at all. The admin log showed a clean
    run of successes and the failures were simply absent, which is the worst
    possible shape for a log: it looks complete.

    This accumulates as the stages complete and is written in a `finally`, so
    a new failure path cannot be added without being recorded. Nothing here
    needs to be remembered by whoever adds one.
    """

    identity: TurnIdentity
    question: str
    started: float
    plan: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    route: str = ""
    citations: list[Citation] = field(default_factory=list)
    failed: str = ""

    def write(self, memory: MemoryStore) -> None:
        memory.record(
            request_id=self.identity.request_id,
            session_id=self.identity.session_id,
            visitor_id=self.identity.visitor_id,
            question_origin=self.identity.question_origin,
            user_message=self.question,
            # Empty when the turn failed, which is what keeps it out of the
            # conversation and in the log.
            assistant_message=self.answer,
            route=self.route,
            tools=[],
            tool_arguments=self.plan,
            citations=self.citations,
            result=self._result(),
            latency_ms=max(0, round((time.monotonic() - self.started) * 1000)),
        )

    def _result(self) -> dict[str, Any]:
        """What happened, for a person reading the log.

        `failed` carries the reason and is present only when there was one, so
        a failed turn cannot be mistaken for a quiet one.
        """
        out: dict[str, Any] = {"execution": self.execution, "answer": {"answer": self.answer}}
        if self.failed:
            out["failed"] = self.failed
        return out


class PlanRejected(Exception):
    """Why the registry would not accept a plan. The cause of the ServiceError."""
