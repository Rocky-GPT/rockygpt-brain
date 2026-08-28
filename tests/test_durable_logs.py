from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

from rockygpt_brain.api.contracts import (
    ChatLogItem,
    FeedbackRequest,
    LogListResponse,
    LogMetrics,
    UnmodifiedResponse,
)
from rockygpt_brain.context.memory import MemoryStore
from rockygpt_brain.context.postgres_logs import (
    PostgresLogStore,
    _redact_text,
    _use_system_ca_store,
)


class FakeDurableLogs:
    def __init__(self, result: LogListResponse | UnmodifiedResponse | None = None) -> None:
        self.records: list[str] = []
        self.closed = False
        self.result = result or _result(client_count=6)

    async def record(self, item: ChatLogItem) -> None:
        self.records.append(str(item.id))

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


async def test_turns_are_flushed_to_the_durable_log_store() -> None:
    durable = FakeDurableLogs()
    memory = MemoryStore(cast(PostgresLogStore, durable))
    memory.record(
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

    await memory.close()

    assert durable.records == ["3dbac48b8b0545a89c85f2e6981c807c"]
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
