from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import cast

import pytest

from rockygpt_brain.api.contracts import (
    ChatLogItem,
    FeedbackRequest,
    LogListResponse,
    LogMetrics,
    UnmodifiedResponse,
)
from rockygpt_brain.context import memory as memory_module
from rockygpt_brain.context.memory import MemoryStore
from rockygpt_brain.context.postgres_logs import (
    PostgresLogStore,
    _redact_text,
    _use_system_ca_store,
)


class FakeDurableLogs:
    def __init__(
        self,
        result: LogListResponse | UnmodifiedResponse | None = None,
        *,
        stall_seconds: float = 0.0,
        raises: Exception | None = None,
    ) -> None:
        self.records: list[str] = []
        self.closed = False
        self.result = result or _result(client_count=6)
        self.stall_seconds = stall_seconds
        self.raises = raises

    async def record(self, item: ChatLogItem) -> None:
        if self.stall_seconds:
            await asyncio.sleep(self.stall_seconds)
        if self.raises is not None:
            raise self.raises
        self.records.append(item.id)

    async def save_feedback(self, feedback: FeedbackRequest) -> None:
        del feedback

    async def set_operator_feedback(self, log_id: str, feedback: str | None) -> bool:
        del log_id, feedback
        return True

    async def list_logs(
        self,
        *,
        search: str | None,
        routes: set[str],
        origins: set[str],
        limit: int,
        version: str | None = None,
    ) -> LogListResponse | UnmodifiedResponse:
        del search, routes, origins, limit, version
        return self.result

    async def changes(self) -> AsyncIterator[str]:
        yield 'data: {"type":"change"}\n\n'

    async def close(self) -> None:
        self.closed = True


def _result(*, client_count: int) -> LogListResponse:
    return LogListResponse(
        logs=[],
        metrics=LogMetrics(
            total_logs=client_count,
            avg_latency_ms=0,
            unique_sessions=client_count,
            unique_visitors=client_count,
            error_count=0,
            client_count=client_count,
            dev_count=0,
            bot_count=0,
        ),
        version="database-version",
    )


async def test_a_turn_is_persisted_before_recording_returns() -> None:
    durable = FakeDurableLogs()
    memory = MemoryStore(cast(PostgresLogStore, durable))
    await memory.record(
        request_id="3dbac48b8b0545a89c85f2e6981c807c",
        session_id="session",
        visitor_id="visitor",
        question_origin="dev",
        user_message="question",
        assistant_message="answer",
        route="general",
        tools=[],
        tool_arguments={},
        citations=[],
        result={},
        latency_ms=10,
    )

    # Asserted before anything is closed. The old design only reached the
    # database on a graceful shutdown, so a check made after `close()` passed
    # either way — and a container that suspends when idle never shuts down
    # gracefully. Recording has to be finished when the call returns.
    assert durable.records == ["3dbac48b8b0545a89c85f2e6981c807c"]

    await memory.close()
    assert durable.closed is True


async def test_log_reads_prefer_the_shared_database() -> None:
    durable = FakeDurableLogs(_result(client_count=6))
    memory = MemoryStore(cast(PostgresLogStore, durable))

    result = await memory.read_logs(
        search=None,
        routes=set(),
        origins=set(),
        limit=100,
        version=None,
    )

    assert isinstance(result, LogListResponse)
    assert result.metrics.client_count == 6
    await memory.close()


def test_sensitive_text_is_redacted_before_database_storage() -> None:
    redacted = _redact_text("email me at student@example.com; password=hunter2")

    assert "student@example.com" not in redacted
    assert "hunter2" not in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_strict_database_tls_uses_the_system_ca_store() -> None:
    import ssl

    full_ssl = _use_system_ca_store("postgresql://db/example?sslmode=verify-full")
    require_ssl = _use_system_ca_store("postgresql://db/example?sslmode=require")
    assert isinstance(full_ssl, ssl.SSLContext)
    assert isinstance(require_ssl, ssl.SSLContext)
    assert _use_system_ca_store("postgresql://db/example?sslmode=disable") is False
    assert _use_system_ca_store("postgresql://db/example") is None


async def _record(memory: MemoryStore, request_id: str) -> None:
    await memory.record(
        request_id=request_id,
        session_id="session",
        visitor_id="visitor",
        question_origin="dev",
        user_message="question",
        assistant_message="answer",
        route="general",
        tools=[],
        tool_arguments={},
        citations=[],
        result={},
        latency_ms=10,
    )


async def test_a_stalled_database_cannot_hold_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    # Awaiting the write is what makes the log worth reading; bounding the wait
    # is what stops that from costing a student their answer when the database
    # is unreachable, or asleep and slow about waking.
    monkeypatch.setattr(memory_module, "PERSIST_TIMEOUT_SECONDS", 0.05)
    durable = FakeDurableLogs(stall_seconds=5.0)
    memory = MemoryStore(cast(PostgresLogStore, durable))

    await asyncio.wait_for(_record(memory, "6f1b1f4e9c4b4a2f8f0b8b2a1d3c4e5f"), timeout=1.0)

    assert durable.records == []


async def test_a_refused_write_is_logged_rather_than_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    durable = FakeDurableLogs(raises=RuntimeError("connection refused"))
    memory = MemoryStore(cast(PostgresLogStore, durable))

    with caplog.at_level(logging.ERROR):
        await _record(memory, "9a2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e")

    assert "Failed to persist chat turn" in caplog.text
    assert durable.records == []
