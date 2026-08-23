"""Repository for the chat_logs table: inserts, feedback upserts, retention,
and the admin log listing (with its change-watermark and metrics)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg

TEXT_RETENTION = timedelta(days=30)
METADATA_RETENTION = timedelta(days=90)

QuestionOrigin = Literal["client", "dev", "bot"]
Feedback = Literal["positive", "negative"]


@dataclass(slots=True)
class ChatLogInput:
    id: str
    session_id: str
    route: str
    visitor_id: str | None = None
    user_message: str = ""
    assistant_message: str = ""
    question_origin: QuestionOrigin | None = None
    tools_invoked: list[str] = field(default_factory=list)
    tool_arguments: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
    facts_extracted: list[dict[str, Any]] = field(default_factory=list)
    debug_info: dict[str, Any] | None = None
    latency_ms: int = 0
    created_at: datetime | None = None


@dataclass(slots=True)
class LogPage:
    logs: list[dict[str, Any]]
    metrics: dict[str, Any]
    version: str


async def insert_chat_log(pool: asyncpg.Pool, entry: ChatLogInput) -> None:
    created_at = entry.created_at or datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO chat_logs (
            id, session_id, visitor_id, user_message, assistant_message, route,
            question_origin, tools_invoked, tool_arguments, citations,
            facts_extracted, debug_info, latency_ms, created_at, updated_at,
            text_expires_at, metadata_expires_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, $11::jsonb,
            $12::jsonb, $13, $14, $14, $15, $16
        )
        ON CONFLICT (id) DO NOTHING
        """,
        entry.id,
        entry.session_id,
        entry.visitor_id,
        entry.user_message,
        entry.assistant_message,
        entry.route,
        entry.question_origin,
        entry.tools_invoked,
        json.dumps(entry.tool_arguments),
        json.dumps(entry.citations),
        json.dumps(entry.facts_extracted),
        json.dumps(entry.debug_info) if entry.debug_info is not None else None,
        entry.latency_ms,
        created_at,
        created_at + TEXT_RETENTION,
        created_at + METADATA_RETENTION,
    )


async def upsert_student_feedback(
    pool: asyncpg.Pool,
    *,
    request_id: str,
    rating: Literal[-1, 1],
    category: str | None,
    comments: str | None,
) -> bool:
    feedback: Feedback = "positive" if rating == 1 else "negative"
    result = await pool.execute(
        """
        UPDATE chat_logs
        SET feedback = $2, feedback_rating = $3, feedback_category = $4,
            feedback_comment = $5, updated_at = now()
        WHERE id = $1
        """,
        request_id,
        feedback,
        rating,
        category,
        comments,
    )
    return _rows_affected(result) > 0


async def set_operator_feedback(
    pool: asyncpg.Pool, *, log_id: str, feedback: Feedback | None
) -> bool:
    result = await pool.execute(
        "UPDATE chat_logs SET feedback = $2, updated_at = now() WHERE id = $1",
        log_id,
        feedback,
    )
    return _rows_affected(result) > 0


async def get_version(pool: asyncpg.Pool) -> str:
    row = await pool.fetchrow(
        "SELECT max(updated_at) AS max_updated, count(*) AS total FROM chat_logs"
    )
    max_updated = row["max_updated"] if row else None
    total = row["total"] if row else 0
    watermark = max_updated.isoformat() if max_updated else "epoch"
    return f"{watermark}:{total}"


async def list_logs(
    pool: asyncpg.Pool,
    *,
    search: str | None = None,
    routes: list[str] | None = None,
    origins: list[str] | None = None,
    limit: int = 100,
) -> LogPage:
    # A single fixed query string, never built from request-derived text:
    # each optional filter is a bound parameter, and an absent filter is
    # expressed as "$n IS NULL" short-circuiting its own clause to true —
    # not by concatenating/interpolating a variable WHERE fragment. The
    # only thing that varies per call is which bound *values* are None,
    # never the SQL text itself.
    #
    # `routes`/`origins` are normalized with `or None` so an empty list
    # means "no filter" (matching the previous dynamic-WHERE behavior),
    # not "filter to zero rows" — `route = ANY('{}')` is unconditionally
    # false for every row, which an empty-but-not-None array parameter
    # would otherwise silently trigger.
    search_pattern = f"%{search}%" if search else None
    rows = await pool.fetch(
        """
        SELECT id, session_id, visitor_id, user_message, assistant_message, route,
               question_origin, tools_invoked, tool_arguments, citations,
               facts_extracted, debug_info, latency_ms, feedback, feedback_rating,
               feedback_category, feedback_comment, created_at
        FROM chat_logs
        WHERE ($1::text IS NULL OR user_message ILIKE $1 OR assistant_message ILIKE $1)
          AND ($2::text[] IS NULL OR route = ANY($2))
          AND ($3::text[] IS NULL OR question_origin = ANY($3))
        ORDER BY created_at DESC
        LIMIT $4
        """,
        search_pattern,
        routes or None,
        origins or None,
        limit,
    )
    logs = [_row_to_log_item(row) for row in rows]
    metrics = await _compute_metrics(pool)
    version = await get_version(pool)
    return LogPage(logs=logs, metrics=metrics, version=version)


async def _compute_metrics(pool: asyncpg.Pool) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        SELECT
            count(*) AS total_logs,
            coalesce(avg(latency_ms), 0) AS avg_latency_ms,
            count(DISTINCT session_id) AS unique_sessions,
            count(DISTINCT visitor_id) FILTER (WHERE visitor_id IS NOT NULL) AS unique_visitors,
            count(*) FILTER (WHERE route = 'error') AS error_count,
            count(*) FILTER (
                WHERE question_origin = 'client' OR question_origin IS NULL
            ) AS client_count,
            count(*) FILTER (WHERE question_origin = 'dev') AS dev_count,
            count(*) FILTER (WHERE question_origin = 'bot') AS bot_count
        FROM chat_logs
        """
    )
    assert row is not None
    return {
        "totalLogs": row["total_logs"],
        "avgLatencyMs": float(row["avg_latency_ms"]),
        "uniqueSessions": row["unique_sessions"],
        "uniqueVisitors": row["unique_visitors"],
        "errorCount": row["error_count"],
        "clientCount": row["client_count"],
        "devCount": row["dev_count"],
        "botCount": row["bot_count"],
    }


def _row_to_log_item(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "visitor_id": row["visitor_id"],
        "user_message": row["user_message"],
        "assistant_message": row["assistant_message"],
        "route": row["route"],
        "question_origin": row["question_origin"],
        "tools_invoked": list(row["tools_invoked"]),
        "tool_arguments": json.loads(row["tool_arguments"]),
        "citations": json.loads(row["citations"]),
        "facts_extracted": json.loads(row["facts_extracted"]),
        "debug_info": json.loads(row["debug_info"]) if row["debug_info"] is not None else None,
        "latency_ms": row["latency_ms"],
        "feedback": row["feedback"],
        "feedback_rating": row["feedback_rating"],
        "feedback_category": row["feedback_category"],
        "feedback_comment": row["feedback_comment"],
        "created_at": row["created_at"],
    }


async def purge_expired(pool: asyncpg.Pool, *, now: datetime | None = None) -> tuple[int, int]:
    """Clear expired text/comment, then delete rows past metadata retention.

    A row is cleared if it has *any* still-unscrubbed text-retention field —
    not just a non-empty message, but also a lingering feedback_comment, so
    a row with already-empty messages and a non-null comment still gets
    scrubbed once text_expires_at passes. Returns
    (text_cleared_count, rows_deleted_count).
    """
    current_time = now or datetime.now(UTC)
    cleared = await pool.execute(
        """
        UPDATE chat_logs
        SET user_message = '', assistant_message = '', feedback_comment = NULL
        WHERE text_expires_at <= $1
          AND (user_message <> '' OR assistant_message <> '' OR feedback_comment IS NOT NULL)
        """,
        current_time,
    )
    deleted = await pool.execute(
        "DELETE FROM chat_logs WHERE metadata_expires_at <= $1", current_time
    )
    return _rows_affected(cleared), _rows_affected(deleted)


def _rows_affected(command_tag: str) -> int:
    # asyncpg command tags look like "UPDATE 3" / "DELETE 1".
    parts = command_tag.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0
