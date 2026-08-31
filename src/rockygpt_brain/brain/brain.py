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
    CAMPUS_DATA,
    DOCUMENTS,
    INSUFFICIENT_EVIDENCE,
    RAG_DISABLED,
    WEB,
    Execution,
    nothing_matched,
)
from rockygpt_brain.brain.plan.run import PlanPort
from rockygpt_brain.brain.plan.schema import Lane
from rockygpt_brain.brain.plan.validate import Rejected, check, route
from rockygpt_brain.brain.resolve.validate import contaminated
from rockygpt_brain.brain.understand.run import UnderstandPort
from rockygpt_brain.brain.understand.schema import Reading, Reference, Understanding
from rockygpt_brain.brain.understand.validate import ResolutionFailed, incoherent, narrowed
from rockygpt_brain.brain.write.run import WritePort
from rockygpt_brain.context.memory import MemoryStore
from rockygpt_brain.errors import ServiceError, Unavailable
from rockygpt_brain.services.data import DataPort
from rockygpt_brain.services.rag.client import RagPort
from rockygpt_brain.services.web import WebPort

RAG_WORK_IN_PROGRESS = "RAG is working progress"


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
        rag_enabled: bool = False,
    ) -> None:
        self._model = model
        self._understand = understand
        self._planner = planner
        self._data = data
        self._web = web
        self._documents = documents
        self._memory = memory
        self._tz = ZoneInfo(timezone)
        self._rag_enabled = rag_enabled

    async def answer(self, request: ChatRequest, identity: TurnIdentity) -> ChatSuccess:
        started = time.monotonic()
        now = (request.now or datetime.now(UTC)).astimezone(self._tz)
        earlier = (
            [turn.model_dump() for turn in request.history]
            if request.history is not None
            else self._memory.history(identity.session_id)
        )

        question = {"question": request.message}
        memory: dict[str, Any] = {
            "currentTime": now.isoformat(),
            "earlierTurns": earlier,
        }
        if request.style_mode:
            memory["styleMode"] = request.style_mode
        if request.response_mode:
            memory["responseMode"] = request.response_mode

        recording = _Recording(identity, request.message, started)
        try:
            return await self._turn(request, identity, now, earlier, question, memory, recording)
        except ServiceError as exc:
            recording.failed = str(exc.__cause__ or exc)
            raise
        except Exception as exc:
            recording.failed = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            await recording.write(self._memory)

    async def _fill(
        self, reading: Reading, earlier: list[dict[str, Any]], current_time: str
    ) -> Understanding:
        """The second reading, and only where the first one asked for it.

        A question that stands on its own never reaches the conversation at
        all: `resolved` is what was asked, because there was nothing in it to
        fill. That is the invariant the split exists for — an earlier turn
        cannot change the meaning of a question that did not point at one,
        since the reading that decided the meaning never saw it.
        """
        if not reading.needs_context:
            return Understanding(
                normalized=reading.normalized,
                uses_context=False,
                resolved=reading.normalized,
            )
        spans = [span.text for span in reading.unresolved]
        resolution = await self._understand.resolve(
            reading.normalized, spans, earlier, current_time
        )
        if problem := contaminated(reading.normalized, spans, resolution, earlier):
            raise Unavailable("Rocky could not work out what that was asking.") from (
                ResolutionFailed(problem)
            )
        return Understanding(
            normalized=reading.normalized,
            references=[
                Reference(text=filled.text, refers_to=filled.refers_to)
                for filled in resolution.references
            ],
            used_turns=resolution.used_turns,
            uses_context=True,
            resolved=resolution.resolved,
        )

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
        reading = narrowed(await self._understand.understand(request.message, now.isoformat()))
        if problem := incoherent(reading):
            raise Unavailable("Rocky could not work out what that was asking.") from (
                ResolutionFailed(problem)
            )
        read = await self._fill(reading, earlier, now.isoformat())
        drafted = await self._planner.plan(read.resolved, now.isoformat())
        semantic_plan = drafted.model_copy(update={"lane": route(drafted)}).summary()
        # Recorded before the plan is judged, so a rejected turn logs the plan
        # that was rejected rather than an empty one. The reason alone says what
        # rule fired; only the plan beside it says what the model actually wrote,
        # and that is the difference between reading a rejection and reproducing
        # it. Diagnosing one such rejection meant querying the log and finding
        # the column blank.
        recording.plan = semantic_plan

        checked = check(drafted, now)
        if isinstance(checked, Rejected):
            raise Unavailable("Rocky could not work out how to answer that.") from PlanRejected(
                checked.reason
            )

        recording.route = checked.lane.value.lower()

        if checked.lane is Lane.RAG and not checked.safety and not self._rag_enabled:
            execution = Execution(
                RAG_DISABLED,
                note="disabled while CODE is being tested",
            )
            recording.execution = execution.summary()
            trace = BrainTrace(
                question=question,
                memory=memory,
                understanding={
                    "normalizedQuestion": read.normalized,
                    "usesContext": read.uses_context,
                    "resolvedQuestion": read.resolved,
                },
                context=_context(read, earlier),
                plan=semantic_plan,
                normalized_plan=checked.summary(),
                execution=execution.summary(),
                answer={
                    "answer": RAG_WORK_IN_PROGRESS,
                    "sufficientEvidence": False,
                },
            )
            response = ChatSuccess(
                request_id=identity.request_id,
                answer=RAG_WORK_IN_PROGRESS,
                route=recording.route,
                citations=[],
                ui_actions=[],
                suggested_questions=[],
                brain_trace=trace,
            )
            recording.answer = response.answer
            return response

        execution = await run(checked, now, self._data, self._web, self._documents)
        recording.execution = execution.summary()

        draft = await self._model.answer(
            request.message,
            earlier,
            now.isoformat(),
            request.style_mode,
            request.response_mode,
            execution.grounding(),
        )

        unsupported = execution.answer_from == DOCUMENTS and not draft.sufficient_evidence
        answer = INSUFFICIENT_EVIDENCE if unsupported else draft.answer

        if (
            execution.answer_from == CAMPUS_DATA
            and not execution.results
            and execution.count is None
            and execution.looked_for.get("filters")
        ):
            answer = nothing_matched(execution.looked_for)

        trace = BrainTrace(
            question=question,
            memory=memory,
            understanding={
                "normalizedQuestion": read.normalized,
                "usesContext": read.uses_context,
                "resolvedQuestion": read.resolved,
            },
            context=_context(read, earlier),
            plan=semantic_plan,
            normalized_plan=execution.normalized_plan or checked.summary(),
            execution=execution.summary(),
            answer={"answer": answer, "sufficientEvidence": draft.sufficient_evidence},
        )
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
    if not read.uses_context:
        return {}
    used = [earlier[i] for i in read.used_turns if 0 <= i < len(earlier)]
    return {
        "references": [r.model_dump(by_alias=True) for r in read.references],
        "contextUsed": used,
    }


def _citations(execution: Execution, now: datetime) -> list[Citation]:
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
    identity: TurnIdentity
    question: str
    started: float
    plan: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    answer: str = ""
    route: str = ""
    citations: list[Citation] = field(default_factory=list)
    failed: str = ""

    async def write(self, memory: MemoryStore) -> None:
        await memory.record(
            request_id=self.identity.request_id,
            session_id=self.identity.session_id,
            visitor_id=self.identity.visitor_id,
            question_origin=self.identity.question_origin,
            user_message=self.question,
            assistant_message=self.answer,
            route=self.route,
            tools=[],
            tool_arguments=self.plan,
            citations=self.citations,
            result=self._result(),
            latency_ms=max(0, round((time.monotonic() - self.started) * 1000)),
        )

    def _result(self) -> dict[str, Any]:
        out: dict[str, Any] = {"execution": self.execution, "answer": {"answer": self.answer}}
        if self.failed:
            out["failed"] = self.failed
        return out


class PlanRejected(Exception):
    pass
