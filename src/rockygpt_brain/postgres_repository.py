"""Production PostgreSQL persistence for the brain-owned runtime state.

The repository deliberately owns only the ``rockygpt_brain`` schema. Public identifiers
arrive here after HMAC pseudonymization at the HTTP boundary; this module never accepts or
stores network identifiers, credentials, or DATA-owned records.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

import asyncpg
from pydantic import ValidationError

from rockygpt_brain.contracts import (
    ChatLogItem,
    ExtractedFact,
    FeedbackRequest,
    LogCitation,
    LogListResponse,
    LogMetrics,
)
from rockygpt_brain.memory import AssistantClaim, MemorySnapshot, MemoryTurn
from rockygpt_brain.persistence import FailedAttempt, SuccessfulTurn
from rockygpt_brain.security import redact_text

_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS rockygpt_brain;

CREATE TABLE IF NOT EXISTS rockygpt_brain.turn_log (
    request_id text PRIMARY KEY,
    session_id text NOT NULL,
    visitor_id text,
    user_message text NOT NULL,
    assistant_message text NOT NULL,
    route text NOT NULL,
    question_origin text NOT NULL
        CHECK (question_origin IN ('client', 'dev', 'bot')),
    tools_invoked jsonb NOT NULL DEFAULT '[]'::jsonb,
    tool_arguments jsonb NOT NULL DEFAULT '{}'::jsonb,
    citations jsonb NOT NULL DEFAULT '[]'::jsonb,
    latency_ms integer NOT NULL CHECK (latency_ms >= 0),
    created_at timestamptz NOT NULL,
    text_expires_at timestamptz NOT NULL,
    metadata_expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS brain_turn_log_created_idx
    ON rockygpt_brain.turn_log (created_at DESC);
CREATE INDEX IF NOT EXISTS brain_turn_log_session_idx
    ON rockygpt_brain.turn_log (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS brain_turn_log_route_origin_idx
    ON rockygpt_brain.turn_log (route, question_origin, created_at DESC);

CREATE TABLE IF NOT EXISTS rockygpt_brain.claim_ledger (
    request_id text NOT NULL REFERENCES rockygpt_brain.turn_log(request_id) ON DELETE CASCADE,
    claim_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    claim_text text NOT NULL,
    evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (request_id, claim_id)
);
CREATE INDEX IF NOT EXISTS brain_claim_ledger_created_idx
    ON rockygpt_brain.claim_ledger (created_at DESC);

CREATE TABLE IF NOT EXISTS rockygpt_brain.evidence_snapshot (
    request_id text NOT NULL REFERENCES rockygpt_brain.turn_log(request_id) ON DELETE CASCADE,
    evidence_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    snapshot jsonb NOT NULL,
    PRIMARY KEY (request_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS rockygpt_brain.failed_attempt (
    request_id text PRIMARY KEY,
    safe_error_code text NOT NULL,
    route text,
    latency_ms integer NOT NULL CHECK (latency_ms >= 0),
    created_at timestamptz NOT NULL,
    metadata_expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS brain_failed_attempt_created_idx
    ON rockygpt_brain.failed_attempt (created_at DESC);

CREATE TABLE IF NOT EXISTS rockygpt_brain.feedback_student (
    request_id text PRIMARY KEY
        REFERENCES rockygpt_brain.turn_log(request_id) ON DELETE CASCADE,
    rating smallint NOT NULL CHECK (rating IN (-1, 1)),
    category text,
    comments text,
    updated_at timestamptz NOT NULL,
    text_expires_at timestamptz NOT NULL,
    metadata_expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_brain.feedback_operator (
    request_id text PRIMARY KEY
        REFERENCES rockygpt_brain.turn_log(request_id) ON DELETE CASCADE,
    feedback text CHECK (feedback IS NULL OR feedback IN ('positive', 'negative')),
    updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_brain.repository_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version bigint NOT NULL CHECK (version >= 0)
);
INSERT INTO rockygpt_brain.repository_state(singleton, version)
VALUES (true, 0)
ON CONFLICT (singleton) DO NOTHING;
"""

_LIST_LOGS_SQL = """
SELECT
    t.*,
    sf.rating AS student_rating,
    sf.category AS student_category,
    sf.comments AS student_comments,
    ofe.feedback AS operator_feedback,
    COALESCE(
        (
            SELECT jsonb_agg(
                jsonb_build_object('claimId', c.claim_id, 'text', c.claim_text)
                ORDER BY c.ordinal
            )
            FROM rockygpt_brain.claim_ledger c
            WHERE c.request_id = t.request_id
        ),
        '[]'::jsonb
    ) AS claims,
    (
        SELECT count(*)
        FROM rockygpt_brain.evidence_snapshot e
        WHERE e.request_id = t.request_id
    ) AS evidence_count
FROM rockygpt_brain.turn_log t
LEFT JOIN rockygpt_brain.feedback_student sf
    ON sf.request_id = t.request_id AND sf.metadata_expires_at > now()
LEFT JOIN rockygpt_brain.feedback_operator ofe ON ofe.request_id = t.request_id
WHERE
    t.metadata_expires_at > now()
    AND (
        $1::text IS NULL
        OR strpos(lower(t.user_message), lower($1)) > 0
        OR strpos(lower(t.assistant_message), lower($1)) > 0
    )
    AND (cardinality($2::text[]) = 0 OR t.route = ANY($2::text[]))
    AND (cardinality($3::text[]) = 0 OR t.question_origin = ANY($3::text[]))
ORDER BY t.created_at DESC, t.request_id DESC
LIMIT $4
"""

_LOG_METRICS_SQL = """
WITH filtered AS (
    SELECT t.*
    FROM rockygpt_brain.turn_log t
    WHERE
        t.metadata_expires_at > now()
        AND (
            $1::text IS NULL
            OR strpos(lower(t.user_message), lower($1)) > 0
            OR strpos(lower(t.assistant_message), lower($1)) > 0
        )
        AND (cardinality($2::text[]) = 0 OR t.route = ANY($2::text[]))
        AND (cardinality($3::text[]) = 0 OR t.question_origin = ANY($3::text[]))
)
SELECT
    count(*) AS total_logs,
    COALESCE(avg(latency_ms), 0)::double precision AS avg_latency_ms,
    count(DISTINCT session_id) AS unique_sessions,
    count(DISTINCT visitor_id) FILTER (WHERE visitor_id IS NOT NULL) AS unique_visitors,
    count(*) FILTER (WHERE question_origin = 'client') AS client_count,
    count(*) FILTER (WHERE question_origin = 'dev') AS dev_count,
    count(*) FILTER (WHERE question_origin = 'bot') AS bot_count,
    (
        SELECT count(*) FROM rockygpt_brain.failed_attempt
        WHERE metadata_expires_at > now()
    ) AS error_count,
    (
        SELECT version FROM rockygpt_brain.repository_state WHERE singleton = true
    ) AS version
FROM filtered
"""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, str):
        try:
            return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _json(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def _json_object(value: object) -> dict[str, Any]:
    decoded = _json(value)
    if not isinstance(decoded, Mapping):
        return {}
    return {str(key): item for key, item in decoded.items()}


def _json_array(value: object) -> list[object]:
    decoded = _json(value)
    if isinstance(decoded, Sequence) and not isinstance(decoded, (str, bytes, bytearray)):
        return list(decoded)
    return []


def _string_array(value: object) -> list[str]:
    return [item for item in _json_array(value) if isinstance(item, str)]


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _change_event() -> str:
    return 'data: {"type":"change"}\n\n'


class PostgresRepository:
    """Asyncpg-backed implementation of :class:`persistence.Repository`.

    ``initialize`` and ``close`` are lifecycle extensions used by the FastAPI lifespan.
    All other public methods conform to the repository protocol.
    """

    def __init__(
        self,
        database_url: str,
        *,
        recent_turn_limit: int = 10,
        claim_limit: int = 100,
        text_retention_days: int = 30,
        metadata_retention_days: int = 90,
        pool_min_size: int = 1,
        pool_max_size: int = 8,
        command_timeout_seconds: float = 10.0,
        heartbeat_seconds: float = 15.0,
        version_poll_seconds: float = 2.0,
    ) -> None:
        if not database_url:
            raise ValueError("database_url must not be empty")
        if recent_turn_limit < 1 or claim_limit < 1:
            raise ValueError("memory limits must be positive")
        if text_retention_days < 1 or metadata_retention_days < text_retention_days:
            raise ValueError("metadata retention must be at least text retention")
        if pool_min_size < 1 or pool_max_size < pool_min_size:
            raise ValueError("invalid PostgreSQL pool bounds")
        if command_timeout_seconds <= 0 or heartbeat_seconds <= 0 or version_poll_seconds <= 0:
            raise ValueError("timeouts must be positive")

        self._database_url = database_url
        self._recent_turn_limit = recent_turn_limit
        self._claim_limit = claim_limit
        self._text_retention = timedelta(days=text_retention_days)
        self._metadata_retention = timedelta(days=metadata_retention_days)
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._command_timeout = command_timeout_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._version_poll_seconds = version_poll_seconds
        self._pool: asyncpg.Pool | None = None
        self._initialize_lock = asyncio.Lock()
        self._change_condition = asyncio.Condition()
        self._version_cache = 0

    async def initialize(self) -> None:
        """Create the pool and brain schema exactly once."""

        async with self._initialize_lock:
            if self._pool is not None:
                return
            pool = await asyncpg.create_pool(
                dsn=self._database_url,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
                command_timeout=self._command_timeout,
            )
            try:
                async with pool.acquire() as connection:
                    await connection.execute(_SCHEMA_SQL)
                    version = await connection.fetchval(
                        "SELECT version FROM rockygpt_brain.repository_state WHERE singleton = true"
                    )
            except BaseException:
                await pool.close()
                raise
            self._pool = pool
            self._version_cache = int(version or 0)

    async def _ready_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            await self.initialize()
        if self._pool is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("PostgreSQL repository did not initialize")
        return self._pool

    async def readiness(self) -> bool:
        try:
            pool = await self._ready_pool()
            async with pool.acquire() as connection:
                value = await connection.fetchval(
                    "SELECT version FROM rockygpt_brain.repository_state WHERE singleton = true"
                )
            await self._observe_version(int(value or 0))
            return value is not None
        except Exception:
            return False

    async def load_memory(self, session_id: str) -> MemorySnapshot:
        """Load bounded, unexpired authoritative turns, claims, and evidence."""

        pool = await self._ready_pool()
        async with pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                recent_rows = await connection.fetch(
                    """
                    SELECT request_id, user_message, assistant_message, route, created_at
                    FROM rockygpt_brain.turn_log
                    WHERE session_id = $1
                      AND text_expires_at > now()
                      AND metadata_expires_at > now()
                    ORDER BY created_at DESC, request_id DESC
                    LIMIT $2
                    """,
                    session_id,
                    self._recent_turn_limit,
                )
                claim_rows = await connection.fetch(
                    """
                    SELECT c.claim_id, c.request_id, c.claim_text, c.evidence_ids, c.created_at
                    FROM rockygpt_brain.claim_ledger c
                    JOIN rockygpt_brain.turn_log t ON t.request_id = c.request_id
                    WHERE t.session_id = $1
                      AND t.text_expires_at > now()
                      AND t.metadata_expires_at > now()
                    ORDER BY c.created_at DESC, t.created_at DESC, c.ordinal DESC
                    LIMIT $2
                    """,
                    session_id,
                    self._claim_limit,
                )
                request_ids = {str(row["request_id"]) for row in (*recent_rows, *claim_rows)}
                referenced_evidence_ids = {
                    evidence_id
                    for row in claim_rows
                    for evidence_id in _string_array(row["evidence_ids"])
                }
                evidence_rows: Sequence[asyncpg.Record] = ()
                if request_ids and referenced_evidence_ids:
                    evidence_rows = await connection.fetch(
                        """
                        SELECT e.request_id, e.snapshot
                        FROM rockygpt_brain.evidence_snapshot e
                        JOIN rockygpt_brain.turn_log t ON t.request_id = e.request_id
                        WHERE e.request_id = ANY($1::text[])
                          AND e.evidence_id = ANY($2::text[])
                          AND t.text_expires_at > now()
                          AND t.metadata_expires_at > now()
                        ORDER BY t.created_at DESC, e.ordinal DESC
                        """,
                        sorted(request_ids),
                        sorted(referenced_evidence_ids),
                    )

        recent: list[MemoryTurn] = []
        for row in reversed(recent_rows):
            created_at = _datetime(row["created_at"])
            if created_at is None:
                continue
            recent.append(
                MemoryTurn(
                    request_id=str(row["request_id"]),
                    user_text=str(row["user_message"]),
                    assistant_text=str(row["assistant_message"]),
                    route=str(row["route"]),
                    created_at=created_at,
                )
            )

        claims: list[AssistantClaim] = []
        for row in reversed(claim_rows):
            created_at = _datetime(row["created_at"])
            if created_at is None:
                continue
            claims.append(
                AssistantClaim(
                    claim_id=str(row["claim_id"]),
                    request_id=str(row["request_id"]),
                    text=str(row["claim_text"]),
                    evidence_ids=tuple(_string_array(row["evidence_ids"])),
                    created_at=created_at,
                )
            )

        historical_evidence: list[dict[str, Any]] = []
        for row in reversed(evidence_rows):
            snapshot = _json_object(row["snapshot"])
            if snapshot:
                snapshot["turnRequestId"] = str(row["request_id"])
                historical_evidence.append(snapshot)

        return MemorySnapshot(
            recent_turns=tuple(recent),
            claims=tuple(claims),
            historical_evidence=tuple(historical_evidence),
        )

    async def commit_success(self, turn: SuccessfulTurn) -> None:
        """Atomically persist the answer log, claim ledger, and evidence snapshots."""

        pool = await self._ready_pool()
        created_at = _utc(turn.created_at)
        citations = [item.model_dump(mode="json", by_alias=True) for item in turn.citations]
        async with pool.acquire() as connection:
            async with connection.transaction():
                inserted = await connection.fetchval(
                    """
                    INSERT INTO rockygpt_brain.turn_log(
                        request_id, session_id, visitor_id, user_message, assistant_message,
                        route, question_origin, tools_invoked, tool_arguments, citations,
                        latency_ms, created_at, text_expires_at, metadata_expires_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10::jsonb,
                        $11, $12, $13, $14
                    )
                    ON CONFLICT (request_id) DO NOTHING
                    RETURNING request_id
                    """,
                    turn.request_id,
                    turn.session_id,
                    turn.visitor_id,
                    turn.user_message,
                    turn.assistant_message,
                    turn.route,
                    turn.question_origin,
                    _dump(list(turn.tools_invoked)),
                    _dump(turn.tool_arguments),
                    _dump(citations),
                    turn.latency_ms,
                    created_at,
                    created_at + self._text_retention,
                    created_at + self._metadata_retention,
                )
                if inserted is None:
                    return

                if turn.claims:
                    await connection.executemany(
                        """
                        INSERT INTO rockygpt_brain.claim_ledger(
                            request_id, claim_id, ordinal, claim_text, evidence_ids, created_at
                        ) VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                        """,
                        [
                            (
                                turn.request_id,
                                claim.claim_id,
                                ordinal,
                                claim.text,
                                _dump(list(claim.evidence_ids)),
                                _utc(claim.created_at),
                            )
                            for ordinal, claim in enumerate(turn.claims)
                        ],
                    )

                evidence_values: list[tuple[str, str, int, str]] = []
                for ordinal, snapshot in enumerate(turn.evidence_snapshot):
                    evidence_id = snapshot.get("evidenceId")
                    if not isinstance(evidence_id, str) or not evidence_id:
                        evidence_id = f"snapshot:{ordinal}"
                    evidence_values.append((turn.request_id, evidence_id, ordinal, _dump(snapshot)))
                if evidence_values:
                    await connection.executemany(
                        """
                        INSERT INTO rockygpt_brain.evidence_snapshot(
                            request_id, evidence_id, ordinal, snapshot
                        ) VALUES ($1, $2, $3, $4::jsonb)
                        """,
                        evidence_values,
                    )
                version = await self._bump(connection)
        await self._observe_version(version)

    async def record_failure(self, attempt: FailedAttempt) -> None:
        """Store only bounded operational fields; failed turns never create memory."""

        pool = await self._ready_pool()
        created_at = _utc(attempt.created_at)
        async with pool.acquire() as connection:
            async with connection.transaction():
                inserted = await connection.fetchval(
                    """
                    INSERT INTO rockygpt_brain.failed_attempt(
                        request_id, safe_error_code, route, latency_ms,
                        created_at, metadata_expires_at
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (request_id) DO NOTHING
                    RETURNING request_id
                    """,
                    attempt.request_id,
                    attempt.safe_error_code,
                    attempt.route,
                    attempt.latency_ms,
                    created_at,
                    created_at + self._metadata_retention,
                )
                if inserted is None:
                    return
                version = await self._bump(connection)
        await self._observe_version(version)

    async def upsert_feedback(self, feedback: FeedbackRequest) -> None:
        pool = await self._ready_pool()
        now = datetime.now(UTC)
        async with pool.acquire() as connection:
            async with connection.transaction():
                persisted = await connection.fetchval(
                    """
                    INSERT INTO rockygpt_brain.feedback_student(
                        request_id, rating, category, comments, updated_at,
                        text_expires_at, metadata_expires_at
                    )
                    SELECT t.request_id, $2, $3, $4, $5, $6, $7
                    FROM rockygpt_brain.turn_log t
                    WHERE t.request_id = $1 AND t.metadata_expires_at > now()
                    ON CONFLICT (request_id) DO UPDATE SET
                        rating = EXCLUDED.rating,
                        category = EXCLUDED.category,
                        comments = EXCLUDED.comments,
                        updated_at = EXCLUDED.updated_at,
                        text_expires_at = EXCLUDED.text_expires_at,
                        metadata_expires_at = EXCLUDED.metadata_expires_at
                    RETURNING request_id
                    """,
                    feedback.request_id,
                    feedback.rating,
                    feedback.category,
                    redact_text(feedback.comments),
                    now,
                    now + self._text_retention,
                    now + self._metadata_retention,
                )
                if persisted is None:
                    # Preserve a non-enumerating success response without storing an orphan row.
                    return
                version = await self._bump(connection)
        await self._observe_version(version)

    async def set_operator_feedback(self, log_id: str, feedback: str | None) -> bool:
        pool = await self._ready_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                updated = await connection.fetchval(
                    """
                    INSERT INTO rockygpt_brain.feedback_operator(request_id, feedback, updated_at)
                    SELECT request_id, $2, now()
                    FROM rockygpt_brain.turn_log
                    WHERE request_id = $1 AND metadata_expires_at > now()
                    ON CONFLICT (request_id) DO UPDATE SET
                        feedback = EXCLUDED.feedback,
                        updated_at = EXCLUDED.updated_at
                    RETURNING request_id
                    """,
                    log_id,
                    feedback,
                )
                if updated is None:
                    return False
                version = await self._bump(connection)
        await self._observe_version(version)
        return True

    async def list_logs(
        self,
        *,
        search: str | None,
        routes: set[str],
        origins: set[str],
        limit: int,
    ) -> LogListResponse:
        pool = await self._ready_pool()
        route_values = sorted(routes)
        origin_values = sorted(origins)
        async with pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                rows = await connection.fetch(
                    _LIST_LOGS_SQL,
                    search,
                    route_values,
                    origin_values,
                    limit,
                )
                metrics = await connection.fetchrow(
                    _LOG_METRICS_SQL,
                    search,
                    route_values,
                    origin_values,
                )

        if metrics is None:  # pragma: no cover - aggregate SELECT always returns one row
            raise RuntimeError("PostgreSQL metrics query returned no row")
        version = int(metrics["version"] or 0)
        await self._observe_version(version)
        return LogListResponse(
            logs=[self._row_to_log(row) for row in rows],
            metrics=LogMetrics(
                total_logs=int(metrics["total_logs"] or 0),
                avg_latency_ms=float(metrics["avg_latency_ms"] or 0.0),
                unique_sessions=int(metrics["unique_sessions"] or 0),
                unique_visitors=int(metrics["unique_visitors"] or 0),
                error_count=int(metrics["error_count"] or 0),
                client_count=int(metrics["client_count"] or 0),
                dev_count=int(metrics["dev_count"] or 0),
                bot_count=int(metrics["bot_count"] or 0),
            ),
            version=str(version),
        )

    def _row_to_log(self, row: asyncpg.Record) -> ChatLogItem:
        citations: list[LogCitation] = []
        for item in _json_array(row["citations"]):
            value = _json_object(item)
            title = value.get("title")
            url = value.get("url")
            if not isinstance(title, str) or not isinstance(url, str):
                continue
            try:
                citations.append(LogCitation(title=title, url=url))
            except ValidationError:
                continue

        facts: list[ExtractedFact] = []
        for item in _json_array(row["claims"]):
            value = _json_object(item)
            claim_id = value.get("claimId")
            text_value = value.get("text")
            if isinstance(claim_id, str) and isinstance(text_value, str):
                facts.append(
                    ExtractedFact(
                        key=claim_id,
                        kind="assistant_claim",
                        value=text_value,
                    )
                )

        origin_value = str(row["question_origin"])
        if origin_value not in {"client", "dev", "bot"}:
            origin_value = "client"
        origin = cast(Literal["client", "dev", "bot"], origin_value)

        operator_value = row["operator_feedback"]
        if operator_value not in {"positive", "negative"}:
            operator_value = None
        operator_feedback = cast(Literal["positive", "negative"] | None, operator_value)

        rating_value = row["student_rating"]
        if rating_value not in {-1, 1}:
            rating_value = None
        student_rating = cast(Literal[-1, 1] | None, rating_value)

        created_at = _datetime(row["created_at"]) or datetime.now(UTC)
        visitor_value = row["visitor_id"]
        return ChatLogItem(
            id=str(row["request_id"]),
            session_id=str(row["session_id"]),
            visitor_id=str(visitor_value) if visitor_value is not None else None,
            user_message=str(row["user_message"]),
            assistant_message=str(row["assistant_message"]),
            route=str(row["route"]),
            question_origin=origin,
            tools_invoked=_string_array(row["tools_invoked"]),
            tool_arguments=_json_object(row["tool_arguments"]),
            citations=citations,
            facts_extracted=facts,
            debug_info={"evidenceCount": int(row["evidence_count"] or 0)},
            latency_ms=max(0, int(row["latency_ms"] or 0)),
            feedback=operator_feedback,
            feedback_rating=student_rating,
            feedback_category=(
                str(row["student_category"]) if row["student_category"] is not None else None
            ),
            feedback_comment=(
                str(row["student_comments"]) if row["student_comments"] is not None else None
            ),
            created_at=created_at,
        )

    def version(self) -> str:
        """Return the last locally observed durable version for ETag checks."""

        return str(self._version_cache)

    async def changes(self) -> AsyncIterator[str]:
        """Yield local write notifications, cross-process polls, and SSE heartbeats."""

        last_version = -1
        heartbeat_at = time.monotonic() + self._heartbeat_seconds
        while True:
            try:
                current = await self._database_version()
            except Exception:
                current = self._version_cache
            if current != last_version:
                last_version = current
                yield _change_event()

            remaining_to_heartbeat = heartbeat_at - time.monotonic()
            if remaining_to_heartbeat <= 0:
                heartbeat_at = time.monotonic() + self._heartbeat_seconds
                yield ": heartbeat\n\n"
                continue

            timeout = min(self._version_poll_seconds, remaining_to_heartbeat)
            async with self._change_condition:
                if self._version_cache == last_version:
                    try:
                        await asyncio.wait_for(self._change_condition.wait(), timeout=timeout)
                    except TimeoutError:
                        pass

    async def purge_expired(self, now: datetime | None = None) -> None:
        """Apply 30-day content and 90-day operational metadata retention."""

        pool = await self._ready_pool()
        cutoff = _utc(now or datetime.now(UTC))
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    DELETE FROM rockygpt_brain.claim_ledger c
                    USING rockygpt_brain.turn_log t
                    WHERE c.request_id = t.request_id AND t.text_expires_at <= $1
                    """,
                    cutoff,
                )
                await connection.execute(
                    """
                    DELETE FROM rockygpt_brain.evidence_snapshot e
                    USING rockygpt_brain.turn_log t
                    WHERE e.request_id = t.request_id AND t.text_expires_at <= $1
                    """,
                    cutoff,
                )
                await connection.execute(
                    """
                    UPDATE rockygpt_brain.turn_log
                    SET user_message = '[EXPIRED]',
                        assistant_message = '[EXPIRED]',
                        tools_invoked = '[]'::jsonb,
                        tool_arguments = '{}'::jsonb,
                        citations = '[]'::jsonb
                    WHERE text_expires_at <= $1
                      AND user_message <> '[EXPIRED]'
                    """,
                    cutoff,
                )
                await connection.execute(
                    """
                    UPDATE rockygpt_brain.feedback_student
                    SET comments = NULL
                    WHERE text_expires_at <= $1 AND comments IS NOT NULL
                    """,
                    cutoff,
                )
                await connection.execute(
                    """
                    DELETE FROM rockygpt_brain.turn_log
                    WHERE metadata_expires_at <= $1
                    """,
                    cutoff,
                )
                await connection.execute(
                    """
                    DELETE FROM rockygpt_brain.failed_attempt
                    WHERE metadata_expires_at <= $1
                    """,
                    cutoff,
                )
                await connection.execute(
                    """
                    DELETE FROM rockygpt_brain.feedback_student
                    WHERE metadata_expires_at <= $1
                    """,
                    cutoff,
                )
                await connection.execute(
                    """
                    DELETE FROM rockygpt_brain.feedback_operator ofe
                    WHERE NOT EXISTS (
                        SELECT 1 FROM rockygpt_brain.turn_log t
                        WHERE t.request_id = ofe.request_id
                    )
                    """,
                )
                version = await self._bump(connection)
        await self._observe_version(version)

    async def _bump(self, connection: asyncpg.Connection) -> int:
        value = await connection.fetchval(
            """
            UPDATE rockygpt_brain.repository_state
            SET version = version + 1
            WHERE singleton = true
            RETURNING version
            """
        )
        if value is None:  # pragma: no cover - initialize guarantees the singleton
            raise RuntimeError("PostgreSQL repository version row is missing")
        return int(value)

    async def _database_version(self) -> int:
        pool = await self._ready_pool()
        async with pool.acquire() as connection:
            value = await connection.fetchval(
                "SELECT version FROM rockygpt_brain.repository_state WHERE singleton = true"
            )
        current = int(value or 0)
        await self._observe_version(current)
        return current

    async def _observe_version(self, version: int) -> None:
        async with self._change_condition:
            # Transactions can complete in one order and resume their Python tasks in another.
            # The durable counter is monotonic, so an older observer must never regress ETags.
            if version > self._version_cache:
                self._version_cache = version
                self._change_condition.notify_all()

    async def close(self) -> None:
        async with self._initialize_lock:
            pool = self._pool
            self._pool = None
            if pool is not None:
                await pool.close()


__all__ = ["PostgresRepository"]
