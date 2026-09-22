"""Real PostgreSQL tests: no SDK calls, production databases, or paid work."""

import json
import os
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from rockygpt_brain.config import MONTHLY_CAP_NUSD, RELEASE, Deployment, Environment
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.provider import ModelResponse, OutputItem, PaidGateway, Usage, open_gateway
from rockygpt_brain.governance.accounting import PaidCallError, PostgresLedger, month_at, reset_at
from rockygpt_brain.governance.reconcile import Receipt, reconcile

NOW = datetime(2026, 9, 30, 23, 59, tzinfo=ZoneInfo("America/New_York"))


@pytest.fixture(scope="module")
def database() -> str:
    url = os.getenv("BRAIN_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set BRAIN_TEST_DATABASE_URL to a disposable local PostgreSQL database")
    parts = conninfo_to_dict(url)
    if parts.get("host") not in {"127.0.0.1", "localhost"} or (
        parts.get("dbname") != "brain_accounting_test"
    ):
        pytest.fail("Accounting tests require localhost/brain_accounting_test")
    with psycopg.connect(url, autocommit=True) as conn:
        exists = conn.execute("SELECT to_regclass('brain_ops.accounts')").fetchone()
        if exists is None or exists[0] is None:
            conn.execute((Path(__file__).parents[1] / "migrations/001_accounting.sql").read_text())
            conn.execute("CREATE ROLE brain_test_development LOGIN IN ROLE brain_development")
            conn.execute("CREATE ROLE brain_test_production LOGIN IN ROLE brain_production")
        if conn.execute("SELECT to_regclass('brain_ops.monthly_allowances')").fetchone() == (None,):
            conn.execute(
                (
                    Path(__file__).parents[1] / "migrations/002_development_monthly_allowance.sql"
                ).read_text()
            )
        definition = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='brain_ops.operations'::regclass "
            "AND conname='operations_category_check'"
        ).fetchone()
        if definition and "routing" not in definition[0]:
            conn.execute(
                (Path(__file__).parents[1] / "migrations/003_routing_accounting.sql").read_text()
            )
    return url


@pytest.fixture
def ledger(database: str) -> Iterator[PostgresLedger]:
    with psycopg.connect(database) as conn:
        conn.execute("TRUNCATE brain_ops.operations, brain_ops.turns, brain_ops.monthly_allowances")
        conn.execute(
            "UPDATE brain_ops.accounts SET paused = false, cap_nusd = %s", (MONTHLY_CAP_NUSD,)
        )
    yield for_environment(database, "development")


def for_environment(url: str, environment: Environment) -> PostgresLedger:
    return PostgresLedger(make_conninfo(url, user="brain_test_" + environment), environment)


def test_transaction_setup_is_applied_before_account_access(ledger: PostgresLedger) -> None:
    with ledger.transaction() as conn:
        row = conn.execute(
            "SELECT current_user AS role, current_setting('statement_timeout') AS statement, "
            "current_setting('lock_timeout') AS lock"
        ).fetchone()
        assert row == {"role": "brain_development", "statement": "2s", "lock": "1500ms"}


def test_failed_role_setup_never_exposes_transaction(database: str) -> None:
    ledger = PostgresLedger(make_conninfo(database, user="brain_test_development"), "production")
    with pytest.raises(PaidCallError, match="accounting_unavailable"):
        with ledger.transaction():
            pytest.fail("A failed role change must not yield an accounting connection")


def reserve(
    ledger: PostgresLedger, amount: int, *, now: datetime = NOW, operation_id: str | None = None
) -> str:
    identity = operation_id or str(uuid4())
    ledger.reserve(identity, "turn", "draft", amount, {"price_version": "test"}, now)
    return identity


def test_session_reuses_connection_without_holding_account_locks(
    ledger: PostgresLedger, database: str
) -> None:
    with ledger.session():
        with ledger.transaction() as first:
            ledger.account(first)
        identity = reserve(ledger, 100)
        assert first.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        # A different connection sees the committed hold and can lock the account
        # while the session waits for the model, without waiting for session close.
        with psycopg.connect(database) as other:
            assert other.execute(
                "SELECT reserved_nusd FROM brain_ops.operations WHERE operation_id=%s",
                (identity,),
            ).fetchone() == (100,)
            assert other.execute(
                "SELECT environment FROM brain_ops.accounts "
                "WHERE environment='development' FOR UPDATE NOWAIT"
            ).fetchone() == ("development",)
        with ledger.transaction() as second:
            assert first is second
    assert first.closed
    # Telemetry after gateway exit still works on a fresh connection.
    assert str(ledger.operations()[0]["operation_id"]) == identity


def test_session_rolls_back_failed_transaction_and_preserves_committed_holds(
    ledger: PostgresLedger,
) -> None:
    with pytest.raises(RuntimeError, match="turn cancelled"):
        with ledger.session():
            identity = reserve(ledger, 100)
            with pytest.raises(PaidCallError, match="accounting_unavailable"):
                with ledger.transaction() as failed:
                    failed.execute("SELECT 1 / 0")
            with ledger.transaction() as recovered:
                assert recovered is failed
                assert ledger.account(recovered)["environment"] == "development"
            assert str(ledger.operations()[0]["operation_id"]) == identity
            raise RuntimeError("turn cancelled")
    assert failed.closed
    assert ledger.operations()[0]["state"] == "reserved"


def test_request_session_cannot_be_shared_between_workers(ledger: PostgresLedger) -> None:
    with ledger.session(), ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(PaidCallError, match="accounting_unavailable"):
            pool.submit(reserve, ledger, 100).result(timeout=3)
        assert ledger.operations() == []
        reserve(ledger, 100)


def test_paid_gateway_reuses_ledger_session_for_readiness_reserve_and_settle(
    ledger: PostgresLedger,
) -> None:
    from test_provider import arguments, response

    deployment = Deployment(
        environment="development", api_key="test-key", project="test-project", ledger_url=ledger.url
    )
    provider = Mock()
    provider.create.return_value = response()
    with (
        patch("rockygpt_brain.provider.OpenAIProvider", return_value=provider),
        patch("rockygpt_brain.accounting.psycopg.connect", wraps=psycopg.connect) as connect,
    ):
        with open_gateway(deployment, "session-test") as gateway:
            gateway.clock = lambda: datetime(2026, 9, 11, tzinfo=ZoneInfo("America/New_York"))
            gateway.create(category="draft", **arguments())
            gateway.finish({"status": "answered"})
        assert connect.call_count == 1
    assert ledger.operations("session-test")[0]["state"] == "settled"


def race_admission(url: str) -> str:
    try:
        reserve(for_environment(url, "development"), MONTHLY_CAP_NUSD // 2 + 1)
        return "admitted"
    except PaidCallError as error:
        return error.code


def test_environment_isolation_and_database_grants(ledger: PostgresLedger, database: str) -> None:
    reserve(ledger, MONTHLY_CAP_NUSD)
    with pytest.raises(PaidCallError, match="budget_exhausted"):
        reserve(ledger, 1)
    production = for_environment(database, "production")
    reserve(production, MONTHLY_CAP_NUSD)
    assert len(ledger.operations()) == len(production.operations()) == 1
    wrong_role = PostgresLedger(ledger.url, "production")
    with pytest.raises(PaidCallError, match="accounting_unavailable"):
        wrong_role.readiness()
    with ledger.transaction() as conn:
        assert (
            conn.execute(
                "SELECT * FROM brain_ops.accounts WHERE environment = 'production'"
            ).fetchone()
            is None
        )
    with pytest.raises(PaidCallError, match="accounting_unavailable"):
        with ledger.transaction() as conn:
            conn.execute("UPDATE brain_ops.accounts SET cap_nusd = 10000000001")


def test_thread_admission_race_cannot_overspend(ledger: PostgresLedger, database: str) -> None:
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(race_admission, [database] * 12))
    assert results.count("admitted") == 1
    assert results.count("budget_exhausted") == 11
    assert len(ledger.operations()) == 1


def test_process_admission_race_cannot_overspend(ledger: PostgresLedger, database: str) -> None:
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
        results = list(pool.map(race_admission, [database] * 8))
    assert results.count("admitted") == 1
    assert results.count("budget_exhausted") == 7
    assert len(ledger.operations()) == 1


def test_restart_and_duplicate_operation_never_execute_again(ledger: PostgresLedger) -> None:
    operation = reserve(ledger, MONTHLY_CAP_NUSD)
    restarted = PostgresLedger(ledger.url, "development")
    with pytest.raises(PaidCallError, match="operation_already_admitted"):
        reserve(restarted, MONTHLY_CAP_NUSD, operation_id=operation)
    with pytest.raises(PaidCallError, match="budget_exhausted"):
        reserve(restarted, 1)


def test_unknown_usage_carries_across_months_until_reconciled(ledger: PostgresLedger) -> None:
    operation = reserve(ledger, MONTHLY_CAP_NUSD)
    ledger.uncertain(operation, "model_timeout", 400)
    next_month = NOW.replace(month=10, day=1, hour=0)
    with pytest.raises(PaidCallError, match="budget_exhausted"):
        reserve(ledger, 1, now=next_month)
    # Proven delayed usage keeps a conservative charge in the new month.
    ledger.settle(operation, 100, {"input_tokens": 1}, "response", "test", 400, next_month)
    reserve(ledger, MONTHLY_CAP_NUSD - 100, now=next_month)
    with pytest.raises(PaidCallError, match="budget_exhausted"):
        reserve(ledger, 1, now=next_month)
    settled = ledger.operations()[0]
    assert str(settled["admitted_month"]) == "2026-09-01"
    assert str(settled["charged_month"]) == "2026-10-01"


def test_settlement_is_idempotent_and_does_not_erase_ambiguous_history(
    ledger: PostgresLedger,
) -> None:
    operation = reserve(ledger, 100)
    ledger.uncertain(operation, "model_timeout", 40)
    ledger.settle(operation, 20, {"input_tokens": 1}, "response", "test", 40, NOW)
    ledger.settle(operation, 20, {"input_tokens": 1}, "response", "test", 40, NOW)
    with pytest.raises(PaidCallError, match="settlement_conflict"):
        ledger.settle(operation, 0, {}, "response", "test", 40, NOW)
    ledger.uncertain(operation, "late_error", 40)
    row = ledger.operations()[0]
    assert row["state"] == "settled"
    assert row["error_code"] == "model_timeout"
    assert row["cost_nusd"] == 20


def test_exact_cap_and_known_zero_usage(ledger: PostgresLedger) -> None:
    operation = reserve(ledger, MONTHLY_CAP_NUSD)
    ledger.settle(operation, 0, {"input_tokens": 0}, "response", "test", 1, NOW)
    reserve(ledger, MONTHLY_CAP_NUSD)
    with pytest.raises(PaidCallError, match="budget_exhausted"):
        reserve(ledger, 1)


def test_provider_overrun_records_actual_liability_and_pauses(ledger: PostgresLedger) -> None:
    operation = reserve(ledger, 100)
    with pytest.raises(PaidCallError, match="accounting_bound_exceeded"):
        ledger.settle(operation, 101, {"input_tokens": 1}, "response", "test", 1, NOW)
    assert ledger.operations()[0]["cost_nusd"] == 101
    with pytest.raises(PaidCallError, match="accounting_paused"):
        reserve(ledger, 1)


def test_new_month_resets_only_settled_charges(ledger: PostgresLedger) -> None:
    operation = reserve(ledger, MONTHLY_CAP_NUSD)
    ledger.settle(operation, MONTHLY_CAP_NUSD, {}, "response", "test", 1, NOW)
    with pytest.raises(PaidCallError, match="budget_exhausted") as error:
        reserve(ledger, 1)
    assert error.value.reset_at == "2026-10-01T00:00:00-04:00"
    reserve(ledger, MONTHLY_CAP_NUSD, now=NOW.replace(month=10, day=1, hour=0))


def test_calendar_months_use_new_york_including_dst_and_year_end() -> None:
    before = datetime(2026, 10, 1, 3, 59, tzinfo=UTC)
    after = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
    assert str(month_at(before)) == "2026-09-01"
    assert str(month_at(after)) == "2026-10-01"
    assert reset_at(datetime(2026, 12, 31, tzinfo=UTC)) == "2027-01-01T00:00:00-05:00"
    assert reset_at(datetime(2026, 3, 15, tzinfo=UTC)) == "2026-04-01T00:00:00-04:00"
    with pytest.raises(ValueError, match="aware"):
        month_at(datetime(2026, 9, 11))


def test_reconciliation_uses_original_price_and_audits_receipt(ledger: PostgresLedger) -> None:
    operation = str(uuid4())
    ledger.reserve(
        operation, "turn", "review", 10000000, {"price": RELEASE.price.model_dump(mode="json")}, NOW
    )
    ledger.uncertain(operation, "usage_unknown", 20)
    receipt = Receipt(
        operation_id=operation,
        provider_response_id="verified-response",
        returned_model="gpt-5.4",
        evidence_reference="provider-export:row-123",
        input_tokens=10,
        cached_input_tokens=0,
        output_tokens=20,
        reasoning_tokens=5,
    )
    reconcile(ledger, receipt, NOW.replace(month=12, day=1))
    reconcile(ledger, receipt, NOW.replace(month=12, day=1))
    row = ledger.operations()[0]
    assert row["cost_nusd"] == 325000
    assert row["metadata"]["reconciliation_reference"] == "provider-export:row-123"
    assert row["error_code"] == "usage_unknown"


def test_durable_turn_reports_are_environment_scoped(ledger: PostgresLedger, database: str) -> None:
    ledger.record_turn("turn", {"costNusd": 23, "fallbackUsed": False})
    with ledger.transaction() as conn:
        assert len(conn.execute("SELECT * FROM brain_ops.turns").fetchall()) == 1
    with for_environment(database, "production").transaction() as conn:
        assert not conn.execute("SELECT * FROM brain_ops.turns").fetchall()


def test_complete_turn_accounts_for_lookup_draft_and_review(ledger: PostgresLedger) -> None:
    provider, data = Mock(), Mock()
    record = {
        "id": "contacts:registrar",
        "title": "Registrar",
        "collection": "contacts",
        "url": "https://www.ramapo.edu/registrar/",
        "freshness": "static",
        "content": "Office D-224",
    }
    call = OutputItem(
        {
            "type": "function_call",
            "call_id": "lookup",
            "name": "search_campus",
            "arguments": json.dumps({"collection": "contacts", "query": "registrar"}),
        }
    )
    candidate = {
        "status": "answered",
        "parts": [
            {"kind": "campus_fact", "text": "The office is D-224.", "evidence_ids": [record["id"]]}
        ],
    }
    review = {
        "parts": [
            {
                "part_index": 0,
                "verdict": "supported",
                "reason": "",
                "unverified_premises": [],
                "uses_event_for_entity": False,
                "infers_food_safety": False,
                "plan_deadlines": [],
            }
        ]
    }
    provider.create.side_effect = [
        ModelResponse(
            "lookup-response", RELEASE.model, "completed", "", [call], Usage(50, 0, 5, 0)
        ),
        ModelResponse(
            "draft-response",
            RELEASE.model,
            "completed",
            json.dumps(candidate),
            [],
            Usage(100, 10, 40, 0),
        ),
        ModelResponse(
            "review-response",
            RELEASE.model,
            "completed",
            json.dumps(review),
            [],
            Usage(150, 20, 60, 30),
        ),
    ]
    data.search.return_value = {
        "status": "ok",
        "dataset_version": "fixture-v1",
        "records": [record],
    }
    gateway = PaidGateway(provider, ledger, "complete-turn", clock=lambda: NOW)
    result = run_turn(
        [ChatMessage(role="user", content="Where is the registrar?")],
        client=gateway,
        data=data,
        model=RELEASE.model,
        now=NOW,
    )
    gateway.finish(
        {
            "status": result["status"],
            "datasetVersion": result["datasetVersion"],
            "retrievalMs": result["metrics"]["retrievalMs"],
        }
    )
    assert result["status"] == "answered"
    operations = ledger.operations("complete-turn")
    assert [row["category"] for row in operations] == ["draft", "draft", "review"]
    assert all(row["state"] == "settled" for row in operations)
    assert sum(row["cost_nusd"] for row in operations) == gateway.usage.report()["costNusd"]
    assert gateway.usage.report()["inputTokens"] == 300
    assert gateway.usage.report()["reasoningTokens"] == 30
    wire_history = provider.create.call_args_list[1].kwargs["input"]
    assert wire_history[1]["call_id"] == wire_history[2]["call_id"] == "lookup"


def test_approved_supplement_expires_and_cannot_affect_production(
    ledger: PostgresLedger, database: str
) -> None:
    with psycopg.connect(database) as conn:
        conn.execute(
            "INSERT INTO brain_ops.monthly_allowances "
            "(environment, month, extra_nusd, approval_note) "
            "VALUES ('development', %s, 20000000000, 'Synthetic test authorization')",
            (month_at(NOW),),
        )
    operation = reserve(ledger, 30_000_000_000)
    with pytest.raises(PaidCallError, match="budget_exhausted"):
        reserve(ledger, 1)
    production = for_environment(database, "production")
    with pytest.raises(PaidCallError, match="budget_exhausted"):
        reserve(production, 10_000_000_001)
    reserve(production, 10_000_000_000)
    ledger.settle(operation, 30_000_000_000, {}, "response", "test", 1, NOW)
    next_month = NOW.replace(month=10, day=1, hour=0)
    reserve(ledger, 10_000_000_000, now=next_month)
    with pytest.raises(PaidCallError, match="budget_exhausted"):
        reserve(ledger, 1, now=next_month)
    assert ledger.operations()[0]["metadata"]["monthly_cap_nusd"] == 30_000_000_000


def test_runtime_cannot_grant_its_own_monthly_supplement(
    ledger: PostgresLedger, database: str
) -> None:
    with pytest.raises(PaidCallError, match="accounting_unavailable"):
        with ledger.transaction() as conn:
            conn.execute(
                "INSERT INTO brain_ops.monthly_allowances "
                "(environment, month, extra_nusd, approval_note) "
                "VALUES ('development', %s, 20000000000, 'Unauthorized')",
                (month_at(NOW),),
            )
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO brain_ops.monthly_allowances "
                "(environment, month, extra_nusd, approval_note) "
                "VALUES ('production', %s, 20000000000, 'Not approved')",
                (month_at(NOW),),
            )


def test_routing_migration_settles_input_only_and_retains_environment_isolation(
    ledger: PostgresLedger, database: str,
) -> None:
    from rockygpt_brain.core.routing import routing_payload
    from test_routing import ENTITY, messages

    now = NOW.replace(day=22)
    jev, provider = Mock(), Mock()
    jev.create.return_value = ModelResponse(
        '', RELEASE.routing.model, 'completed', '{}', [], Usage(100, 0, 500, 0),
    )
    gateway = PaidGateway(provider, ledger, 'jev-postgres', routing_provider=jev, clock=lambda: now)
    gateway.route(routing_payload(messages(), [ENTITY], now)[0], timeout=2)
    operation = ledger.operations('jev-postgres')[0]
    assert operation['category'] == 'routing'
    assert operation['state'] == 'settled' and operation['cost_nusd'] == 4200
    assert operation['metadata']['provider'] == 'typesafe'
    assert operation['metadata']['price']['output_nusd'] == 0
    assert for_environment(database, 'production').operations('jev-postgres') == []
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE brain_ops.operations SET category='unaccounted' "
                         "WHERE request_id='jev-postgres'")


def test_routing_monthly_budget_rejects_before_provider_call(ledger: PostgresLedger) -> None:
    from rockygpt_brain.core.routing import routing_payload
    from test_routing import ENTITY, messages

    now = NOW.replace(day=22)
    reserve(ledger, MONTHLY_CAP_NUSD, now=now)
    jev = Mock()
    gateway = PaidGateway(Mock(), ledger, 'blocked-jev', routing_provider=jev, clock=lambda: now)
    with pytest.raises(PaidCallError, match='budget_exhausted'):
        gateway.route(routing_payload(messages(), [ENTITY], now)[0], timeout=2)
    jev.create.assert_not_called()
