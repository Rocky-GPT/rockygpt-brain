"""Brain-owned persistence port with bounded retention and an in-memory test backend."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Protocol

from rockygpt_brain.contracts import (
    ChatLogItem,
    Citation,
    ExtractedFact,
    FeedbackRequest,
    LogCitation,
    LogListResponse,
    LogMetrics,
)
from rockygpt_brain.memory import AssistantClaim, MemorySnapshot, MemoryTurn, MutableMemory
from rockygpt_brain.security import redact_text


@dataclass(frozen=True, slots=True)
class SuccessfulTurn:
    request_id: str
    session_id: str
    visitor_id: str | None
    user_message: str
    assistant_message: str
    route: str
    question_origin: str
    tools_invoked: tuple[str, ...]
    tool_arguments: dict[str, Any]
    citations: tuple[Citation, ...]
    claims: tuple[AssistantClaim, ...]
    evidence_snapshot: tuple[dict[str, Any], ...]
    latency_ms: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FailedAttempt:
    request_id: str
    safe_error_code: str
    route: str | None
    latency_ms: int
    created_at: datetime


@dataclass(slots=True)
class StoredFeedback:
    rating: int
    category: str | None
    comments: str | None


class Repository(Protocol):
    async def readiness(self) -> bool: ...

    async def load_memory(self, session_id: str) -> MemorySnapshot: ...

    async def commit_success(self, turn: SuccessfulTurn) -> None: ...

    async def record_failure(self, attempt: FailedAttempt) -> None: ...

    async def upsert_feedback(self, feedback: FeedbackRequest) -> None: ...

    async def set_operator_feedback(self, log_id: str, feedback: str | None) -> bool: ...

    async def list_logs(
        self,
        *,
        search: str | None,
        routes: set[str],
        origins: set[str],
        limit: int,
    ) -> LogListResponse: ...

    def version(self) -> str: ...

    async def changes(self) -> AsyncIterator[str]: ...

    async def purge_expired(self, now: datetime | None = None) -> None: ...


class InMemoryRepository:
    """Deterministic backend for tests/local development; never stores raw public IDs."""

    def __init__(
        self,
        *,
        recent_turn_limit: int = 10,
        claim_limit: int = 100,
        text_retention_days: int = 30,
        metadata_retention_days: int = 90,
    ) -> None:
        self._recent_limit = recent_turn_limit
        self._claim_limit = claim_limit
        self._text_retention = timedelta(days=text_retention_days)
        self._metadata_retention = timedelta(days=metadata_retention_days)
        self._memories: dict[str, MutableMemory] = {}
        self._turns: list[SuccessfulTurn] = []
        self._failures: list[FailedAttempt] = []
        self._student_feedback: dict[str, StoredFeedback] = {}
        self._operator_feedback: dict[str, str | None] = {}
        self._version = 0
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)

    async def readiness(self) -> bool:
        return True

    async def load_memory(self, session_id: str) -> MemorySnapshot:
        async with self._lock:
            return self._memories.get(session_id, MutableMemory()).snapshot()

    async def commit_success(self, turn: SuccessfulTurn) -> None:
        async with self._condition:
            self._turns.append(turn)
            memory = self._memories.setdefault(turn.session_id, MutableMemory())
            memory.append(
                MemoryTurn(
                    request_id=turn.request_id,
                    user_text=turn.user_message,
                    assistant_text=turn.assistant_message,
                    route=turn.route,
                    created_at=turn.created_at,
                ),
                list(turn.claims),
                list(turn.evidence_snapshot),
                recent_limit=self._recent_limit,
                claim_limit=self._claim_limit,
            )
            self._version += 1
            self._condition.notify_all()

    async def record_failure(self, attempt: FailedAttempt) -> None:
        async with self._condition:
            self._failures.append(attempt)
            self._version += 1
            self._condition.notify_all()

    async def upsert_feedback(self, feedback: FeedbackRequest) -> None:
        async with self._condition:
            self._student_feedback[feedback.request_id] = StoredFeedback(
                rating=feedback.rating,
                category=feedback.category,
                comments=redact_text(feedback.comments),
            )
            self._version += 1
            self._condition.notify_all()

    async def set_operator_feedback(self, log_id: str, feedback: str | None) -> bool:
        async with self._condition:
            if not any(turn.request_id == log_id for turn in self._turns):
                return False
            self._operator_feedback[log_id] = feedback
            self._version += 1
            self._condition.notify_all()
            return True

    def version(self) -> str:
        return str(self._version)

    async def list_logs(
        self,
        *,
        search: str | None,
        routes: set[str],
        origins: set[str],
        limit: int,
    ) -> LogListResponse:
        async with self._lock:
            lowered = search.casefold() if search else None
            selected = [
                turn
                for turn in reversed(self._turns)
                if (not routes or turn.route in routes)
                and (not origins or turn.question_origin in origins)
                and (
                    lowered is None
                    or lowered in turn.user_message.casefold()
                    or lowered in turn.assistant_message.casefold()
                )
            ]
            page = selected[:limit]
            items = [self._public_item(turn) for turn in page]
            origins_count = {origin: 0 for origin in ("client", "dev", "bot")}
            for turn in selected:
                origins_count[turn.question_origin] = origins_count.get(turn.question_origin, 0) + 1
            average = sum(turn.latency_ms for turn in selected) / len(selected) if selected else 0.0
            return LogListResponse(
                logs=items,
                metrics=LogMetrics(
                    totalLogs=len(selected),
                    avgLatencyMs=average,
                    uniqueSessions=len({turn.session_id for turn in selected}),
                    uniqueVisitors=len({turn.visitor_id for turn in selected if turn.visitor_id}),
                    errorCount=len(self._failures),
                    clientCount=origins_count["client"],
                    devCount=origins_count["dev"],
                    botCount=origins_count["bot"],
                ),
                version=self.version(),
            )

    def _public_item(self, turn: SuccessfulTurn) -> ChatLogItem:
        student = self._student_feedback.get(turn.request_id)
        return ChatLogItem(
            id=turn.request_id,
            session_id=turn.session_id,
            visitor_id=turn.visitor_id,
            user_message=turn.user_message,
            assistant_message=turn.assistant_message,
            route=turn.route,
            question_origin=turn.question_origin,
            tools_invoked=list(turn.tools_invoked),
            tool_arguments=turn.tool_arguments,
            citations=[LogCitation(title=item.title, url=item.url) for item in turn.citations],
            facts_extracted=[
                ExtractedFact(key=claim.claim_id, kind="assistant_claim", value=claim.text)
                for claim in turn.claims
            ],
            debug_info={"evidenceCount": len(turn.evidence_snapshot)},
            latency_ms=turn.latency_ms,
            feedback=self._operator_feedback.get(turn.request_id),
            feedback_rating=student.rating if student else None,
            feedback_category=student.category if student else None,
            feedback_comment=student.comments if student else None,
            created_at=turn.created_at,
        )

    async def changes(self) -> AsyncIterator[str]:
        last = -1
        while True:
            async with self._condition:
                if self._version == last:
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=15.0)
                    except TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                last = self._version
            yield 'data: {"type":"change"}\n\n'

    async def purge_expired(self, now: datetime | None = None) -> None:
        cutoff_now = now or datetime.now(timezone.utc)
        text_cutoff = cutoff_now - self._text_retention
        metadata_cutoff = cutoff_now - self._metadata_retention
        async with self._condition:
            retained: list[SuccessfulTurn] = []
            for turn in self._turns:
                if turn.created_at < metadata_cutoff:
                    continue
                if turn.created_at < text_cutoff:
                    retained.append(
                        replace(
                            turn,
                            user_message="[EXPIRED]",
                            assistant_message="[EXPIRED]",
                            claims=(),
                            evidence_snapshot=(),
                        )
                    )
                else:
                    retained.append(turn)
            self._turns = retained
            self._failures = [item for item in self._failures if item.created_at >= metadata_cutoff]
            active_requests = {turn.request_id for turn in self._turns}
            self._student_feedback = {
                key: value for key, value in self._student_feedback.items() if key in active_requests
            }
            self._operator_feedback = {
                key: value for key, value in self._operator_feedback.items() if key in active_requests
            }
            for memory in self._memories.values():
                memory.recent_turns = [
                    turn for turn in memory.recent_turns if turn.created_at >= text_cutoff
                ]
                memory.claims = [claim for claim in memory.claims if claim.created_at >= text_cutoff]
                active_turns = {turn.request_id for turn in memory.recent_turns}
                memory.historical_evidence = [
                    item
                    for item in memory.historical_evidence
                    if item.get("turnRequestId") in active_turns
                ]
            self._memories = {
                key: memory
                for key, memory in self._memories.items()
                if memory.recent_turns or memory.claims
            }
            self._version += 1
            self._condition.notify_all()
