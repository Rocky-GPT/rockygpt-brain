"""The spending cap against a real, disposable PostgreSQL with the production migrations."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import psycopg
import pytest

from rockygpt_brain.spending import (
    CAMPUS_TIMEZONE,
    PostgresLedger,
    SpendingError,
    close_pools,
    paid_call,
)

NOW = datetime(2026, 9, 30, 23, 59, tzinfo=CAMPUS_TIMEZONE)
NEXT_MONTH = datetime(2026, 10, 1, 0, 1, tzinfo=CAMPUS_TIMEZONE)
DOLLAR = 1_000_000_000


def ledger(database: str, environment: Any = "development") -> PostgresLedger:
    return PostgresLedger(database, environment)


def state(admin: psycopg.Connection[Any], operation: str) -> tuple[Any, ...] | None:
    return admin.execute(
        "SELECT state, cost_nusd, charged_month::text FROM brain_ops.operations "
        "WHERE operation_id = %s", (operation,)).fetchone()


def test_a_hold_within_the_cap_is_admitted(admin: Any, database: str) -> None:
    operation = ledger(database).hold("r1", "routing", DOLLAR // 2, {}, NOW)
    assert state(admin, operation) == ("reserved", None, None)


def test_a_hold_past_the_cap_is_refused_until_next_month(admin: Any, database: str) -> None:
    dev = ledger(database)
    dev.hold("r1", "routing", DOLLAR, {}, NOW)
    with pytest.raises(SpendingError) as refused:
        dev.hold("r2", "routing", 1, {}, NOW)
    assert refused.value.code == "budget_exhausted"
    assert refused.value.reset_at == "2026-10-01T00:00:00-04:00"


def test_settling_charges_the_real_cost_and_frees_the_rest(admin: Any, database: str) -> None:
    dev = ledger(database)
    operation = dev.hold("r1", "routing", DOLLAR, {}, NOW)
    dev.settle(operation, DOLLAR // 4, {"input_tokens": 10}, "resp", "jev-1.13.0", 900, NOW)
    assert state(admin, operation) == ("settled", DOLLAR // 4, "2026-09-01")
    dev.hold("r2", "routing", DOLLAR * 3 // 4, {}, NOW)
    with pytest.raises(SpendingError):
        dev.hold("r3", "routing", 1, {}, NOW)


def test_costing_more_than_was_held_pauses_spending(admin: Any, database: str) -> None:
    dev = ledger(database)
    operation = dev.hold("r1", "routing", 100, {}, NOW)
    with pytest.raises(SpendingError) as overrun:
        dev.settle(operation, 101, {}, "resp", "jev", 10, NOW)
    assert overrun.value.code == "accounting_bound_exceeded"
    assert state(admin, operation) == ("settled", 101, "2026-09-01")
    with pytest.raises(SpendingError) as paused:
        dev.hold("r2", "routing", 1, {}, NOW)
    assert paused.value.code == "accounting_paused"


def test_an_uncertain_hold_counts_against_its_own_month_only(admin: Any, database: str) -> None:
    dev = ledger(database)
    operation = dev.hold("r1", "routing", DOLLAR, {}, NOW)
    dev.uncertain(operation, "timeout", 2000)
    assert state(admin, operation) == ("uncertain", None, None)
    with pytest.raises(SpendingError):
        dev.hold("r2", "routing", 1, {}, NOW)
    dev.hold("r3", "routing", DOLLAR, {}, NEXT_MONTH)


def test_a_hold_still_in_flight_counts_in_the_next_month(admin: Any, database: str) -> None:
    dev = ledger(database)
    dev.hold("r1", "routing", DOLLAR, {}, NOW)
    with pytest.raises(SpendingError):
        dev.hold("r2", "routing", 1, {}, NEXT_MONTH)


def test_a_development_supplement_raises_that_month_only(admin: Any, database: str) -> None:
    admin.execute("INSERT INTO brain_ops.monthly_allowances (environment, month, extra_nusd, "
                  "approval_note) VALUES ('development', '2026-09-01', %s, 'test')", (DOLLAR,))
    dev = ledger(database)
    dev.hold("r1", "routing", DOLLAR * 2, {}, NOW)
    with pytest.raises(SpendingError):
        dev.hold("r2", "routing", 1, {}, NOW)


def test_each_environment_spends_only_its_own_allowance(admin: Any, database: str) -> None:
    ledger(database, "production").hold("r1", "routing", DOLLAR, {}, NOW)
    ledger(database, "development").hold("r2", "routing", DOLLAR, {}, NOW)


def test_holds_made_at_once_never_pass_the_cap(admin: Any, database: str) -> None:
    dev = ledger(database)

    def attempt(index: int) -> bool:
        try:
            dev.hold(f"r{index}", "routing", DOLLAR // 4, {}, NOW)
            return True
        except SpendingError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = list(pool.map(attempt, range(8)))
    assert sum(admitted) == 4


def test_a_paid_call_settles_what_it_reports(admin: Any, database: str) -> None:
    with paid_call(ledger(database), "r1", "routing", 1000, NOW) as receipt:
        receipt.cost, receipt.model = 420, "jev-1.13.0"
    (row,) = admin.execute("SELECT state, cost_nusd, returned_model FROM brain_ops.operations"
                           ).fetchall()
    assert row == ("settled", 420, "jev-1.13.0")


def test_a_paid_call_that_fails_before_its_cost_is_known_stays_uncertain(
        admin: Any, database: str) -> None:
    with pytest.raises(TimeoutError), paid_call(ledger(database), "r1", "routing", 1000, NOW):
        raise TimeoutError
    with pytest.raises(SpendingError) as unknown, paid_call(
            ledger(database), "r2", "routing", 1000, NOW):
        pass
    assert unknown.value.code == "usage_unknown"
    rows = admin.execute("SELECT request_id, state, error_code FROM brain_ops.operations "
                         "ORDER BY request_id").fetchall()
    assert rows == [("r1", "uncertain", "TimeoutError"), ("r2", "uncertain", "usage_unknown")]


def test_a_paid_call_that_fails_after_its_cost_is_known_still_settles(
        admin: Any, database: str) -> None:
    with pytest.raises(ValueError), paid_call(ledger(database), "r1", "routing", 1000,
                                              NOW) as receipt:
        receipt.cost = 42
        raise ValueError("unreadable answer")
    assert admin.execute("SELECT state, cost_nusd FROM brain_ops.operations").fetchall() == [
        ("settled", 42)]


def test_an_unreachable_ledger_refuses_to_spend() -> None:
    unreachable = PostgresLedger("postgresql://nobody@127.0.0.1:1/none", "development")
    with pytest.raises(SpendingError) as refused:
        unreachable.hold("r1", "routing", 1, {}, NOW)
    assert refused.value.code == "accounting_unavailable"
    close_pools()


def test_a_hold_needs_a_price() -> None:
    with pytest.raises(SpendingError) as refused:
        PostgresLedger("postgresql://unused", "development").hold("r1", "routing", 0, {}, NOW)
    assert refused.value.code == "price_unavailable"
