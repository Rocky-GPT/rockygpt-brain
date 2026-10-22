"""Optional real concurrency/RLS test against an empty disposable local database.

Set BRAIN_GATEWAY_TEST_DATABASE_URL to a NEW localhost database whose name starts
with brain_gateway_test. This creates schema and cluster roles; never use a shared
cluster. The normal offline test suite skips this explicit integration check.
"""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from rockygpt_brain.spending import PostgresLedger, Reservation, SpendingError

NOW = datetime(2026, 10, 1, 16, tzinfo=UTC)
PREVIOUS_MONTH = datetime(2026, 9, 30, 16, tzinfo=UTC)


def test_real_postgres_concurrent_reservations_environment_and_month_isolation() -> None:
    url = os.getenv("BRAIN_GATEWAY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires an explicitly provided disposable PostgreSQL database")
    params = conninfo_to_dict(url)
    assert params.get("host") in {"localhost", "127.0.0.1"}
    assert str(params.get("dbname", "")).startswith("brain_gateway_test")
    asyncio.run(_exercise(url))


async def _exercise(url: str) -> None:
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as admin:
        cursor = await admin.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'brain_ops'",
        )
        assert await cursor.fetchone() is None, "test requires an empty disposable database"
        migrations = Path(__file__).parents[1] / "migrations"
        for migration in sorted(migrations.glob("*.sql")):
            await admin.execute(migration.read_text())
        await admin.execute("CREATE ROLE gateway_test_dev LOGIN")
        await admin.execute("CREATE ROLE gateway_test_prod LOGIN")
        await admin.execute("GRANT brain_development TO gateway_test_dev")
        await admin.execute("GRANT brain_production TO gateway_test_prod")
        dev_url = make_conninfo(url, user="gateway_test_dev")
        prod_url = make_conninfo(url, user="gateway_test_prod")
        development = PostgresLedger(dev_url, "development")
        second_worker = PostgresLedger(dev_url, "development")
        production = PostgresLedger(prod_url, "production")
        forbidden = PostgresLedger(dev_url, "production")
        overprivileged = PostgresLedger(url, "development")
        ledgers = [development, second_worker, production, forbidden, overprivileged]
        for ledger in ledgers:
            await ledger.open()
        try:
            assert await development.ready()
            assert await production.ready()
            assert not await forbidden.ready()
            assert not await overprivileged.ready()
            with pytest.raises(SpendingError, match="ledger_unavailable"):
                await forbidden.reserve("cross-environment", 1, {}, NOW)
            # Every worker races against the same $10 account. Exactly ten $1
            # reservations fit, even with 30 callers split between two pools.
            results = await asyncio.gather(*(
                (development if index % 2 else second_worker).reserve(
                    f"request-{index}", 1_000_000_000, {}, PREVIOUS_MONTH,
                ) for index in range(30)
            ), return_exceptions=True)
            reservations = [item for item in results if isinstance(item, Reservation)]
            failures = [item for item in results if isinstance(item, SpendingError)]
            assert len(reservations) == 10
            assert len(failures) == 20
            assert all(item.code == "budget_exhausted" for item in failures)
            # Production remains independent; September's unresolved calls still
            # consume development allowance after the October boundary.
            await production.reserve("production-request", 1_000_000_000, {}, NOW)
            with pytest.raises(SpendingError, match="budget_exhausted"):
                await development.reserve("october-request", 1, {}, NOW)
            await development.uncertain(reservations[0], "timeout")
            with pytest.raises(SpendingError, match="budget_exhausted"):
                await development.reserve("after-uncertain", 1, {}, NOW)
            # Settlement releases only unused reservation; one $0.75 reservation
            # fits after settling a prior $1 hold to $0.25 in the current month.
            await development.settle(reservations[0], 250_000_000, {}, "id", "model", NOW)
            await development.reserve("after-settlement", 750_000_000, {}, NOW)
            with pytest.raises(SpendingError, match="budget_exhausted"):
                await development.reserve("after-settlement-full", 1, {}, NOW)
            # Application roles cannot change the cap or create a supplement.
            async with await psycopg.AsyncConnection.connect(dev_url, autocommit=True) as app:
                await app.execute("SET ROLE brain_development")
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await app.execute("UPDATE brain_ops.accounts SET cap_nusd = 20000000000")
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await app.execute(
                        "INSERT INTO brain_ops.monthly_allowances "
                        "(environment, month, extra_nusd, approval_note) "
                        "VALUES ('development', '2026-10-01', 1000000000, 'not approved')",
                    )
            # A restart must preserve the budget commitments.
            await second_worker.close()
            restarted = PostgresLedger(dev_url, "development")
            await restarted.open()
            try:
                with pytest.raises(SpendingError, match="budget_exhausted"):
                    await restarted.reserve("restart", 1, {}, NOW)
            finally:
                await restarted.close()
            # Overruns are charged and atomically pause further admissions.
            await development.settle(reservations[1], 2_000_000_000, {}, "id", "model", NOW)
            with pytest.raises(SpendingError, match="account_paused"):
                await development.reserve("after-overrun", 1, {}, NOW)
            assert not await development.ready()
            cursor = await admin.execute(
                "SELECT SUM(CASE WHEN state = 'settled' THEN cost_nusd ELSE reserved_nusd END) "
                "FROM brain_ops.operations WHERE environment = 'development'",
            )
            row: Any = await cursor.fetchone()
            assert row[0] == 11_000_000_000
        finally:
            for ledger in ledgers:
                await ledger.close()
