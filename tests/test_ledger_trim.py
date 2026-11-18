"""The ledger trim against a real PostgreSQL database (skipped without one, run in CI).

It makes its own database next to BRAIN_GATEWAY_TEST_DATABASE_URL's, so it never touches the
empty one the gateway test needs, and drops it afterwards.
"""

import asyncio
import os
import uuid
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from rockygpt_brain.ledger_trim import trim_ledger

DATABASE = "brain_gateway_test_trim"


def test_trim_needs_more_than_a_month_kept() -> None:
    with pytest.raises(ValueError):
        asyncio.run(trim_ledger(None, kept=timedelta(days=20)))  # type: ignore[arg-type]


def test_trim_deletes_only_settled_history_past_the_window() -> None:
    url = os.getenv("BRAIN_GATEWAY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires an explicitly provided disposable PostgreSQL database")
    params = conninfo_to_dict(url)
    assert params.get("host") in {"localhost", "127.0.0.1"}
    asyncio.run(_exercise(url))


async def _exercise(url: str) -> None:
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as admin:
        await admin.execute(f"DROP DATABASE IF EXISTS {DATABASE}")
        await admin.execute(f"CREATE DATABASE {DATABASE}")
        cursor = await admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'brain_development'")
        roles_existed = await cursor.fetchone() is not None
    trim_url = make_conninfo(url, dbname=DATABASE)
    roles_exist = True
    try:
        async with await psycopg.AsyncConnection.connect(trim_url, autocommit=True) as db:
            roles_exist = roles_existed
            for migration in sorted((Path(__file__).parents[1] / "migrations").glob("*.sql")):
                text = migration.read_text()
                if roles_exist:  # roles are cluster-wide; the gateway test may have made them
                    text = "\n".join(line for line in text.splitlines()
                                     if not line.startswith("CREATE ROLE"))
                await db.execute(text)
                roles_exist = True

            async def operation(state: str, days_old: int) -> None:
                settled = state == "settled"
                await db.execute(
                    "INSERT INTO brain_ops.operations (environment, operation_id, request_id, "
                    "category, admitted_month, charged_month, reserved_nusd, cost_nusd, state, "
                    "metadata, created_at, updated_at) VALUES ('production', %s, %s, 'draft', "
                    "date_trunc('month', now())::date, "
                    "CASE WHEN %s THEN date_trunc('month', now())::date END, 100, "
                    "CASE WHEN %s THEN 90 END, %s, '{}'::jsonb, "
                    "now() - make_interval(days => %s), now() - make_interval(days => %s))",
                    (uuid.uuid4(), f"r-{uuid.uuid4()}", settled, settled, state, days_old,
                     days_old))

            async def turn(days_old: int) -> None:
                await db.execute(
                    "INSERT INTO brain_ops.turns (environment, request_id, summary, created_at) "
                    "VALUES ('development', %s, '{}'::jsonb, now() - make_interval(days => %s))",
                    (f"t-{uuid.uuid4()}", days_old))

            for _ in range(5):
                await operation("settled", 90)
            await operation("settled", 10)
            await operation("reserved", 90)
            await operation("uncertain", 90)
            for _ in range(3):
                await turn(90)
            await turn(5)

            counted = await trim_ledger(db, dry_run=True, batch=2)
            assert (counted.operations, counted.turns, counted.dry_run) == (5, 3, True)

            done = await trim_ledger(db, batch=2)
            assert (done.operations, done.turns) == (5, 3)
            assert done.database_bytes > 0

            cursor = await db.execute(
                "SELECT state, count(*) FROM brain_ops.operations GROUP BY state ORDER BY state")
            assert await cursor.fetchall() == [("reserved", 1), ("settled", 1),
                                               ("uncertain", 1)]
            cursor = await db.execute("SELECT count(*) FROM brain_ops.turns")
            assert await cursor.fetchone() == (1,)
            again = await trim_ledger(db)
            assert (again.operations, again.turns) == (0, 0)
    finally:
        async with await psycopg.AsyncConnection.connect(url, autocommit=True) as admin:
            await admin.execute(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)")
            if not roles_existed:  # leave the cluster as found, for the gateway test
                await admin.execute("DROP ROLE IF EXISTS brain_development, brain_production")
