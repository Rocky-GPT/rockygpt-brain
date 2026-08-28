from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import ssl
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.parse import parse_qs, urlsplit

import asyncpg

from rockygpt_brain.api.contracts import (
    ChatLogItem,
    ExtractedFact,
    FeedbackRequest,
    LogCitation,
    LogListResponse,
    LogMetrics,
    UnmodifiedResponse,
)

_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS rockygpt_brain;

CREATE TABLE IF NOT EXISTS rockygpt_brain.chat_logs (
  id UUID PRIMARY KEY,
  session_id TEXT NOT NULL,
  visitor_id TEXT NOT NULL,
  user_message TEXT NOT NULL,
  assistant_message TEXT NOT NULL,
  route TEXT NOT NULL,
  question_origin TEXT NOT NULL DEFAULT 'client',
  tools_invoked JSONB NOT NULL DEFAULT '[]'::jsonb,
  tool_arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
  citations JSONB NOT NULL DEFAULT '[]'::jsonb,
  facts_extracted JSONB NOT NULL DEFAULT '[]'::jsonb,
  debug_info JSONB NOT NULL DEFAULT '{}'::jsonb,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  feedback TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + interval '30 days')
);

CREATE TABLE IF NOT EXISTS rockygpt_brain.feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id TEXT NOT NULL UNIQUE,
  rating SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
  category TEXT,
  comments TEXT,
  question TEXT NOT NULL DEFAULT '',
  answer TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + interval '90 days')
);

CREATE INDEX IF NOT EXISTS chat_logs_created_at_idx
  ON rockygpt_brain.chat_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS chat_logs_session_id_idx
  ON rockygpt_brain.chat_logs(session_id);
CREATE INDEX IF NOT EXISTS chat_logs_expires_at_idx
  ON rockygpt_brain.chat_logs(expires_at);
CREATE INDEX IF NOT EXISTS feedback_expires_at_idx
  ON rockygpt_brain.feedback(expires_at);

DELETE FROM rockygpt_brain.chat_logs WHERE expires_at <= NOW();
DELETE FROM rockygpt_brain.feedback WHERE expires_at <= NOW();
"""

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_STUDENT_ID = re.compile(r"\bR\d{8}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
_SECRET = re.compile(
    r"\b(password|passcode|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|secret)"
    r"\s*[:=]\s*([^\s,;]+)",
    re.IGNORECASE,
)


def _redact_text(value: str) -> str:
    value = _EMAIL.sub("[REDACTED_EMAIL]", value)
    value = _SSN.sub("[REDACTED_SSN]", value)
    value = _STUDENT_ID.sub("[REDACTED_STUDENT_ID]", value)
    value = _PHONE.sub("[REDACTED_PHONE]", value)
    return _SECRET.sub(lambda match: f"{match.group(1)}: [REDACTED_SECRET]", value)


def _redact_json(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_json(item) for key, item in value.items()}
    return value


def _decoded(value: object, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return value


def _use_system_ca_store(database_url: str) -> ssl.SSLContext | bool | None:
    """Use Python's trusted CA store for strict libpq-style TLS modes.

    asyncpg otherwise expects a PostgreSQL-specific ``~/.postgresql/root.crt``
    file for these modes, even when the operating system already trusts the CA.
    """
    ssl_mode = parse_qs(urlsplit(database_url).query).get("sslmode", [""])[-1].casefold()
    if ssl_mode in {"verify-ca", "verify-full", "require"}:
        return ssl.create_default_context()
    if ssl_mode == "disable":
        return False
    if "ssl=true" in database_url.lower() or "sslmode=" in database_url.lower():
        return ssl.create_default_context()
    return None


def _mapping(value: object) -> dict[str, Any]:
    decoded = _decoded(value, {})
    if not isinstance(decoded, Mapping):
        return {}
    return {str(key): item for key, item in decoded.items()}


def _sequence(value: object) -> list[Any]:
    decoded = _decoded(value, [])
    if isinstance(decoded, Sequence) and not isinstance(decoded, (str, bytes, bytearray)):
        return list(decoded)
    return []


class PostgresLogStore:
    """Durable reader/writer for the existing production chat-log schema."""

    def __init__(
        self,
        database_url: str,
        *,
        hash_key: str,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
    ) -> None:
        if not database_url:
            raise ValueError("database_url must not be empty")
        if not hash_key:
            raise ValueError("hash_key must not be empty")
        self._database_url = database_url
        self._hash_key = hash_key.encode()
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool: asyncpg.Pool | None = None
        self._initialize_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._pool is not None:
                return
            pool = await asyncpg.create_pool(
                dsn=self._database_url,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
                command_timeout=15,
                ssl=_use_system_ca_store(self._database_url),
            )
            try:
                async with pool.acquire() as connection:
                    await connection.execute(_SCHEMA_SQL)
            except BaseException:
                await pool.close()
                raise
            self._pool = pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _ready_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            await self.initialize()
        if self._pool is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("PostgreSQL log store did not initialize")
        return self._pool

    def _pseudonymous_id(self, value: str) -> str:
        digest = hmac.new(self._hash_key, value.encode(), hashlib.sha256).hexdigest()
        return f"v1_{digest}"

    async def record(self, item: ChatLogItem) -> None:
        pool = await self._ready_pool()
        citations = [citation.model_dump(mode="json") for citation in item.citations]
        facts = [fact.model_dump(mode="json") for fact in item.facts_extracted]
        await pool.execute(
            """
            INSERT INTO rockygpt_brain.chat_logs (
              id, session_id, visitor_id, user_message, assistant_message, route,
              question_origin, tools_invoked, tool_arguments, citations,
              facts_extracted, debug_info, latency_ms, feedback, created_at
            ) VALUES (
              $1::uuid, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10::jsonb,
              $11::jsonb, $12::jsonb, $13, $14, $15
            )
            ON CONFLICT (id) DO UPDATE SET
              feedback = COALESCE(EXCLUDED.feedback, rockygpt_brain.chat_logs.feedback)
            """,
            item.id,
            self._pseudonymous_id(item.session_id),
            self._pseudonymous_id(item.visitor_id or item.session_id),
            _redact_text(item.user_message),
            _redact_text(item.assistant_message),
            item.route,
            item.question_origin or "client",
            json.dumps(_redact_json(item.tools_invoked)),
            json.dumps(_redact_json(item.tool_arguments)),
            json.dumps(_redact_json(citations)),
            json.dumps(_redact_json(facts)),
            json.dumps(_redact_json(item.debug_info or {})),
            item.latency_ms,
            item.feedback,
            item.created_at,
        )

    async def save_feedback(self, feedback: FeedbackRequest) -> None:
        pool = await self._ready_pool()
        await pool.execute(
            """
            INSERT INTO rockygpt_brain.feedback (
              request_id, rating, category, comments, question, answer
            )
            SELECT ($1::uuid)::text, $2, $3, $4, user_message, assistant_message
            FROM rockygpt_brain.chat_logs
            WHERE id = $1::uuid
            ON CONFLICT (request_id) DO UPDATE SET
              rating = EXCLUDED.rating,
              category = EXCLUDED.category,
              comments = EXCLUDED.comments,
              question = EXCLUDED.question,
              answer = EXCLUDED.answer,
              created_at = NOW(),
              expires_at = NOW() + interval '90 days'
            """,
            feedback.request_id,
            feedback.rating,
            feedback.category,
            _redact_text(feedback.comments) if feedback.comments else None,
        )

    async def set_operator_feedback(self, log_id: str, feedback: str | None) -> bool:
        pool = await self._ready_pool()
        result = await pool.execute(
            "UPDATE rockygpt_brain.chat_logs SET feedback = $1 WHERE id = $2::uuid",
            feedback,
            log_id,
        )
        return str(result) == "UPDATE 1"

    async def current_version(self) -> str:
        pool = await self._ready_pool()
        row = await pool.fetchrow(
            """
            SELECT
              (SELECT COUNT(*)::text FROM rockygpt_brain.chat_logs
                WHERE expires_at > NOW()) AS log_count,
              (SELECT COALESCE(EXTRACT(EPOCH FROM MAX(created_at)), 0)::text
                FROM rockygpt_brain.chat_logs WHERE expires_at > NOW()) AS max_log_ts,
              (SELECT COALESCE(MAX(id::text), '') FROM rockygpt_brain.chat_logs
                WHERE expires_at > NOW()) AS latest_log_id,
              (SELECT COUNT(*)::text FROM rockygpt_brain.feedback
                WHERE expires_at > NOW()) AS feedback_count,
              (SELECT COALESCE(EXTRACT(EPOCH FROM MAX(created_at)), 0)::text
                FROM rockygpt_brain.feedback WHERE expires_at > NOW()) AS max_feedback_ts
            """
        )
        return (
            f"v_{row['log_count']}_{row['max_log_ts']}_{row['latest_log_id']}"
            f"_{row['feedback_count']}_{row['max_feedback_ts']}"
        )

    async def list_logs(
        self,
        *,
        search: str | None,
        routes: set[str],
        origins: set[str],
        limit: int,
        version: str | None = None,
    ) -> LogListResponse | UnmodifiedResponse:
        pool = await self._ready_pool()
        current = await self.current_version()
        if version == current:
            return UnmodifiedResponse()

        clauses = ["t.expires_at > NOW()"]
        values: list[object] = []

        def add(clause: str, value: object) -> None:
            values.append(value)
            clauses.append(clause.format(position=len(values)))

        if search:
            add(
                "(t.user_message ILIKE ${position} OR t.assistant_message ILIKE ${position} "
                "OR t.session_id ILIKE ${position})",
                f"%{search}%",
            )
        if routes:
            add("t.route = ANY(${position}::text[])", sorted(routes))
        if origins:
            add("t.question_origin = ANY(${position}::text[])", sorted(origins))

        where = " AND ".join(clauses)
        log_values = [*values, limit]
        log_query = f"""
            SELECT
              t.id, t.session_id, t.visitor_id, t.user_message, t.assistant_message,
              t.route, t.question_origin, t.tools_invoked, t.tool_arguments,
              t.citations, t.facts_extracted, t.debug_info, t.latency_ms, t.feedback,
              f.rating AS feedback_rating, f.category AS feedback_category,
              f.comments AS feedback_comment, t.created_at
            FROM rockygpt_brain.chat_logs t
            LEFT JOIN rockygpt_brain.feedback f
              ON f.request_id = t.id::text AND f.expires_at > NOW()
            WHERE {where}
            ORDER BY t.created_at DESC
            LIMIT ${len(log_values)}
            """
        rows = await pool.fetch(
            log_query,
            *log_values,
        )
        metrics_query = f"""
            SELECT
              COUNT(*)::int AS total_logs,
              COALESCE(AVG(t.latency_ms), 0)::float8 AS avg_latency_ms,
              COUNT(DISTINCT t.session_id)::int AS unique_sessions,
              COUNT(DISTINCT t.visitor_id)::int AS unique_visitors,
              COUNT(*) FILTER (WHERE t.route ILIKE '%error%')::int AS error_count,
              COUNT(*) FILTER (WHERE t.question_origin = 'client')::int AS client_count,
              COUNT(*) FILTER (WHERE t.question_origin = 'dev')::int AS dev_count,
              COUNT(*) FILTER (WHERE t.question_origin = 'bot')::int AS bot_count
            FROM rockygpt_brain.chat_logs t
            WHERE {where}
            """
        metrics = await pool.fetchrow(
            metrics_query,
            *values,
        )
        return LogListResponse(
            logs=[self._public_item(row) for row in rows],
            metrics=LogMetrics(
                total_logs=int(metrics["total_logs"] or 0),
                avg_latency_ms=float(metrics["avg_latency_ms"] or 0),
                unique_sessions=int(metrics["unique_sessions"] or 0),
                unique_visitors=int(metrics["unique_visitors"] or 0),
                error_count=int(metrics["error_count"] or 0),
                client_count=int(metrics["client_count"] or 0),
                dev_count=int(metrics["dev_count"] or 0),
                bot_count=int(metrics["bot_count"] or 0),
            ),
            version=current,
        )

    def _public_item(self, row: asyncpg.Record) -> ChatLogItem:
        citations = []
        for value in _sequence(row["citations"]):
            if not isinstance(value, Mapping) or not value.get("title") or not value.get("url"):
                continue
            citations.append(LogCitation(title=str(value["title"]), url=cast(str, value["url"])))

        facts = []
        for value in _sequence(row["facts_extracted"]):
            if not isinstance(value, Mapping):
                continue
            if not all(key in value for key in ("key", "kind", "value")):
                continue
            facts.append(
                ExtractedFact(
                    key=str(value["key"]),
                    kind=str(value["kind"]),
                    value=value["value"],
                )
            )

        origin = str(row["question_origin"] or "client")
        if origin not in {"client", "dev", "bot"}:
            origin = "client"
        feedback = row["feedback"]
        if feedback not in {"positive", "negative"}:
            feedback = None
        created_at = row["created_at"]
        if not isinstance(created_at, datetime):
            created_at = datetime.now(UTC)

        return ChatLogItem(
            id=str(row["id"]),
            session_id=str(row["session_id"]),
            visitor_id=str(row["visitor_id"]) if row["visitor_id"] else None,
            user_message=str(row["user_message"]),
            assistant_message=str(row["assistant_message"]),
            route=str(row["route"]),
            question_origin=cast(Literal["client", "dev", "bot"], origin),
            tools_invoked=[str(value) for value in _sequence(row["tools_invoked"])],
            tool_arguments=_mapping(row["tool_arguments"]),
            citations=citations,
            facts_extracted=facts,
            debug_info=_mapping(row["debug_info"]),
            latency_ms=max(0, int(row["latency_ms"] or 0)),
            feedback=cast(Literal["positive", "negative"] | None, feedback),
            feedback_rating=row["feedback_rating"],
            feedback_category=row["feedback_category"],
            feedback_comment=row["feedback_comment"],
            created_at=created_at,
        )

    async def changes(self) -> AsyncIterator[str]:
        last: str | None = None
        last_heartbeat = time.monotonic()
        while True:
            current = await self.current_version()
            if current != last:
                last = current
                last_heartbeat = time.monotonic()
                yield 'data: {"type":"change"}\n\n'
            elif time.monotonic() - last_heartbeat >= 15:
                last_heartbeat = time.monotonic()
                yield ": heartbeat\n\n"
            await asyncio.sleep(2)
