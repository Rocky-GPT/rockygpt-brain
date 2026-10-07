"""The ledger's connection pool must not hand a request a connection the database already closed."""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import AsyncConnectionPool

from rockygpt_brain import spending
from rockygpt_brain.spending import POOL_MAX_IDLE_SECONDS, PostgresLedger, SpendingError

NOW = datetime(2026, 10, 7, 16, tzinfo=UTC)


def test_the_pool_checks_a_connection_when_it_is_taken_and_drops_idle_ones_early(
        monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    # The pool's own check, read off the real class before the class is replaced.
    check = AsyncConnectionPool.check_connection

    class RecordingPool:
        check_connection = check

        def __init__(self, *_: Any, **kwargs: Any) -> None:
            seen.update(kwargs)

    monkeypatch.setattr(spending, "AsyncConnectionPool", RecordingPool)
    PostgresLedger("host=localhost dbname=x", "development")
    assert seen["check"] is check
    # Shorter than a database's idle timeout (Neon closes idle connections after a few minutes).
    assert seen["max_idle"] == POOL_MAX_IDLE_SECONDS <= 180
    # What made the ledger fail closed is unchanged: a short wait, no unbounded retry.
    assert seen["timeout"] == 3 and seen["max_size"] == 4 and seen["min_size"] == 0


def test_a_database_that_cannot_be_reached_still_refuses_and_never_admits() -> None:
    ledger = PostgresLedger(
        "host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1", "development")

    async def attempt() -> None:
        await ledger.open()
        try:
            assert await ledger.ready() is False
            with pytest.raises(SpendingError) as refused:
                await ledger.reserve("r1", 1_000, {}, NOW)
            assert refused.value.code == "ledger_unavailable"
        finally:
            await ledger.close()

    asyncio.run(attempt())


def test_a_connection_the_server_closed_does_not_fail_the_next_request() -> None:
    url = os.getenv("BRAIN_GATEWAY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires an explicitly provided disposable PostgreSQL database")
    params = conninfo_to_dict(url)
    assert params.get("host") in {"localhost", "127.0.0.1"}
    assert str(params.get("dbname", "")).startswith("brain_gateway_test")
    asyncio.run(_closed_by_the_server(url))


async def _closed_by_the_server(url: str) -> None:
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as admin:
        cursor = await admin.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'brain_ops'")
        assert await cursor.fetchone() is None, "test requires an empty disposable database"
        for migration in sorted((Path(__file__).parents[1] / "migrations").glob("*.sql")):
            await admin.execute(migration.read_text())
        await admin.execute("CREATE ROLE gateway_pool_dev LOGIN")
        await admin.execute("GRANT brain_development TO gateway_pool_dev")
        ledger = PostgresLedger(make_conninfo(url, user="gateway_pool_dev"), "development")
        await ledger.open()
        try:
            assert await ledger.ready()  # A connection is now sitting idle in the pool.
            # The server closes it, as Neon does after a quiet spell.
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE usename = 'gateway_pool_dev' AND pid <> pg_backend_pid()")
            await asyncio.sleep(0.2)
            assert await ledger.ready()  # Without the check this is the 503.
            reservation = await ledger.reserve("pool-1", 1_000, {}, NOW)
            assert reservation.amount_nusd == 1_000
        finally:
            await ledger.close()
