"""Durable reservation accounting in a separate PostgreSQL schema. Amounts are USD nanodollars."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from datetime import date, datetime
from threading import Lock, get_ident
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from rockygpt_brain.config import (
    DEVELOPMENT_SUPPLEMENT_CAP_NUSD,
    MONTHLY_CAP_NUSD,
    RELEASE,
    ConfigurationError,
    Environment,
)

CAMPUS_ZONE = ZoneInfo("America/New_York")
Category = Literal["draft", "review", "routing"]
Statement = tuple[str | sql.Composable, tuple[Any, ...]]
IDLE = psycopg.pq.TransactionStatus.IDLE
LedgerConnection = psycopg.Connection[dict[str, Any]]
# The pool's warnings print a connection's host and login name; the Brain logs error
# classes only, and a failed checkout still surfaces as accounting_unavailable.
for _pool_logger in ("psycopg.pool", "psycopg_pool"):
    logging.getLogger(_pool_logger).setLevel(logging.ERROR)
_POOLS: dict[str, ConnectionPool[LedgerConnection]] = {}
_POOLS_LOCK = Lock()


def ledger_pool(url: str) -> ConnectionPool[LedgerConnection]:
    """Ledger connections kept open between turns, one pool per database URL.

    Opening a connection costs TCP, TLS and login round trips (about 0.6 s from the
    development laptop), and every turn used to open two: one for its session and
    one to record the turn after it. A pooled connection costs one round trip to
    check it still works, and a broken one is replaced. Neon counts only running
    queries as activity, so idle pooled connections don't keep it awake; it closes
    them when it scales to zero, and the check catches that. The pool also closes
    connections left unused for a few minutes. Each turn holds one at a time.
    """
    with _POOLS_LOCK:
        pool = _POOLS.get(url)
        if pool is None:
            pool = ConnectionPool(
                url,
                connection_class=LedgerConnection,
                kwargs={"connect_timeout": 2, "row_factory": dict_row, "autocommit": True},
                min_size=0,
                max_size=RELEASE.active_turns + 2,
                max_idle=240,
                timeout=3,
                check=ConnectionPool.check_connection,
                name="ledger",
                open=True,
            )
            _POOLS[url] = pool
        return pool


def close_ledger_pools() -> None:
    """Close every pooled ledger connection; tests use it to start from an empty pool."""
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


class PaidCallError(Exception):
    def __init__(self, code: str, *, reset_at: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.reset_at = reset_at


def month_at(now: datetime) -> date:
    if now.tzinfo is None:
        raise ValueError("Accounting requires an aware clock")
    return now.astimezone(CAMPUS_ZONE).date().replace(day=1)


def reset_at(now: datetime) -> str:
    month = month_at(now)
    year, number = (month.year + 1, 1) if month.month == 12 else (month.year, month.month + 1)
    return datetime(year, number, 1, tzinfo=CAMPUS_ZONE).isoformat()


# Admits a hold in one statement. It runs after the account lock, so it sees every hold
# committed before it. Parameters: the allowance's environment and month; the duplicate
# check's environment and operation; the committed spend's month and environment; the
# account's environment; the new row; then the account cap ceiling, the supplement
# ceiling, whether supplements apply to this environment, and the amount.
RESERVE = (
    "WITH facts AS ("
    " SELECT account.cap_nusd, account.paused,"
    " (SELECT extra_nusd FROM brain_ops.monthly_allowances"
    "  WHERE environment = %s AND month = %s) AS extra_nusd,"
    " EXISTS (SELECT 1 FROM brain_ops.operations"
    "  WHERE environment = %s AND operation_id = %s) AS existing,"
    " (SELECT COALESCE(SUM(CASE WHEN state <> 'settled' THEN reserved_nusd"
    "  WHEN charged_month >= %s THEN cost_nusd ELSE 0 END), 0)"
    "  FROM brain_ops.operations WHERE environment = %s) AS committed"
    " FROM brain_ops.accounts AS account WHERE account.environment = %s"
    "), limits AS ("
    " SELECT facts.*, COALESCE(facts.extra_nusd, 0) AS extra,"
    " facts.cap_nusd + COALESCE(facts.extra_nusd, 0) AS cap FROM facts"
    "), admitted AS ("
    " INSERT INTO brain_ops.operations (environment, operation_id, request_id, category,"
    " admitted_month, reserved_nusd, metadata)"
    " SELECT %s, %s, %s, %s, %s, %s, %s || jsonb_build_object('monthly_cap_nusd', cap)"
    " FROM limits WHERE cap_nusd > 0 AND cap_nusd <= %s"
    " AND extra BETWEEN 0 AND %s AND (extra = 0 OR %s)"
    " AND NOT existing AND NOT paused AND committed + %s <= cap"
    " RETURNING operation_id"
    ") SELECT facts.*, EXISTS (SELECT 1 FROM admitted) AS admitted FROM facts"
)


# Settles a hold in one statement. Delayed usage is charged conservatively in the
# settlement month; the admission month is kept for provider reconciliation. An overrun
# still records the actual liability, and pauses the account. Parameters: the settled
# values, the operation, the account cap ceiling, then the account and the cost.
SETTLE = (
    "WITH settled AS ("
    " UPDATE brain_ops.operations SET state = 'settled', cost_nusd = %s, usage = %s,"
    " charged_month = GREATEST(admitted_month, %s), provider_response_id = %s,"
    " returned_model = %s, elapsed_ms = %s, metadata = metadata || %s,"
    " updated_at = clock_timestamp()"
    " WHERE environment = %s AND operation_id = %s AND state <> 'settled'"
    " AND EXISTS (SELECT 1 FROM brain_ops.accounts"
    "  WHERE environment = %s AND cap_nusd > 0 AND cap_nusd <= %s)"
    " RETURNING reserved_nusd"
    "), paused AS ("
    " UPDATE brain_ops.accounts SET paused = true"
    " WHERE environment = %s AND EXISTS (SELECT 1 FROM settled WHERE reserved_nusd < %s)"
    " RETURNING environment"
    ") SELECT (SELECT count(*) FROM settled) AS settled"
)


class Ledger(Protocol):
    def reserve(
        self,
        operation_id: str,
        request_id: str,
        category: Category,
        amount: int,
        metadata: dict[str, Any],
        now: datetime,
    ) -> None: ...

    def settle(
        self,
        operation_id: str,
        cost: int,
        usage: dict[str, int],
        response_id: str,
        model: str,
        elapsed_ms: int,
        now: datetime,
        reconciliation_reference: str | None = None,
    ) -> None: ...

    def uncertain(self, operation_id: str, code: str, elapsed_ms: int) -> None: ...

    def record_turn(self, request_id: str, summary: dict[str, Any]) -> None: ...

    def pause(self) -> None: ...


class PostgresLedger:
    def __init__(self, url: str, environment: Environment) -> None:
        self.url = url
        self.environment = environment
        self._session: LedgerConnection | None = None
        self._session_thread: int | None = None

    @contextmanager
    def session(self) -> Iterator[None]:
        """Reuse one connection for a synchronous turn, never an open transaction."""
        if self._session is not None:
            raise PaidCallError("accounting_unavailable")
        try:
            with ledger_pool(self.url).connection() as conn:
                self._session = conn
                self._session_thread = get_ident()
                try:
                    yield
                finally:
                    self._session = None
                    self._session_thread = None
        except psycopg.Error as error:
            raise PaidCallError("accounting_unavailable") from error

    @staticmethod
    def batch(
        conn: psycopg.Connection[dict[str, Any]], *statements: Statement
    ) -> list[list[dict[str, Any]]]:
        """Run statements in order as one message: one network round trip, not one each.

        The ledger database can be far from the Brain (the development laptop reaches it
        in about 90 ms), so the number of trips sets the ledger's share of every paid
        call. psycopg binds each statement's parameters on the client. Returns each
        statement's rows, empty for commands. An error stops the rest of the message.
        """
        query = sql.SQL("; ").join(
            sql.SQL(text) if isinstance(text, str) else text for text, _ in statements
        )
        params = [value for _, values in statements for value in values]
        results: list[list[dict[str, Any]]] = []
        with psycopg.ClientCursor(conn, row_factory=dict_row) as cursor:
            cursor.execute(query, params or None)
            while True:
                results.append(cursor.fetchall() if cursor.description else [])
                if not cursor.nextset():
                    break
        return results

    @contextmanager
    def opened(
        self, *statements: Statement
    ) -> Iterator[tuple[psycopg.Connection[dict[str, Any]], list[list[dict[str, Any]]]]]:
        """A transaction whose first message also carries `statements`; yields their rows.

        One round trip opens the transaction, applies its role and limits and runs the
        statements. A failed setup stops the message before any statement runs and never
        exposes the transaction. Leaving the block commits, unless a statement already
        did; an exception rolls back.
        """
        if self._session is not None and self._session_thread != get_ident():
            raise PaidCallError("accounting_unavailable")
        try:
            connection = (
                nullcontext(self._session)
                if self._session is not None
                else ledger_pool(self.url).connection()
            )
            with connection as conn:
                try:
                    setup: list[Statement] = [
                        ("BEGIN", ()),
                        (sql.SQL("SET LOCAL ROLE {}").format(
                            sql.Identifier("brain_" + self.environment)), ()),
                        ("SET LOCAL statement_timeout = '2000ms'", ()),
                        ("SET LOCAL lock_timeout = '1500ms'", ()),
                    ]
                    results = self.batch(conn, *setup, *statements)
                    yield conn, results[len(setup):]
                except BaseException:
                    if not conn.closed and conn.info.transaction_status != IDLE:
                        with suppress(psycopg.Error):
                            conn.execute("ROLLBACK")
                    raise
                if conn.info.transaction_status != IDLE:
                    conn.execute("COMMIT")
        except psycopg.Error as error:
            raise PaidCallError("accounting_unavailable") from error

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with self.opened() as (conn, _):
            yield conn

    def lock_account(self) -> Statement:
        """Serializes all writers for this environment; reads batched after it see
        everything committed before the lock was granted."""
        return (
            "SELECT * FROM brain_ops.accounts WHERE environment = %s FOR UPDATE",
            (self.environment,),
        )

    @staticmethod
    def checked_account(rows: list[dict[str, Any]]) -> dict[str, Any]:
        row = rows[0] if rows else None
        if row is None or not 0 < row["cap_nusd"] <= MONTHLY_CAP_NUSD:
            raise PaidCallError("accounting_unavailable")
        return row

    def account(self, conn: psycopg.Connection[dict[str, Any]]) -> dict[str, Any]:
        return self.checked_account(self.batch(conn, self.lock_account())[0])

    def readiness(self) -> None:
        with self.opened(
            self.lock_account(),
            ("SELECT month FROM brain_ops.monthly_allowances LIMIT 0", ()),
            ("SELECT operation_id FROM brain_ops.operations LIMIT 0", ()),
            ("SELECT request_id FROM brain_ops.turns LIMIT 0", ()),
            ("COMMIT", ()),  # Read-only, so checking after the commit changes nothing.
        ) as (_, (account, *_)):
            self.checked_account(account)

    def allowance(self, now: datetime) -> Statement:
        return (
            "SELECT extra_nusd FROM brain_ops.monthly_allowances "
            "WHERE environment = %s AND month = %s",
            (self.environment, month_at(now)),
        )

    def monthly_cap(
        self, conn: psycopg.Connection[dict[str, Any]], account: dict[str, Any], now: datetime
    ) -> int:
        return self.cap_with(account, self.batch(conn, self.allowance(now))[0])

    def cap_with(self, account: dict[str, Any], rows: list[dict[str, Any]]) -> int:
        extra = int(rows[0]["extra_nusd"]) if rows else 0
        if not 0 <= extra <= DEVELOPMENT_SUPPLEMENT_CAP_NUSD or (
            extra and self.environment != "development"
        ):
            raise PaidCallError("accounting_unavailable")
        return int(account["cap_nusd"]) + extra

    def pause(self) -> None:
        with self.opened(
            self.lock_account(),
            ("UPDATE brain_ops.accounts SET paused = true WHERE environment = %s",
             (self.environment,)),
        ) as (_, (account, _)):
            self.checked_account(account)  # Otherwise the update rolls back.

    def reserve(
        self,
        operation_id: str,
        request_id: str,
        category: Category,
        amount: int,
        metadata: dict[str, Any],
        now: datetime,
    ) -> None:
        if amount <= 0:
            raise PaidCallError("price_unavailable")
        # One round trip: the account lock, then a statement that reads what the checks
        # need and inserts the hold only if they all pass, then COMMIT. The lock is never
        # held across a trip. The checks below repeat the insert's conditions in the same
        # order, only to name the refusal.
        with self.opened(
            self.lock_account(),
            (RESERVE, (
                self.environment, month_at(now), self.environment, operation_id,
                month_at(now), self.environment, self.environment,
                self.environment, operation_id, request_id, category, month_at(now), amount,
                Jsonb(metadata), MONTHLY_CAP_NUSD, DEVELOPMENT_SUPPLEMENT_CAP_NUSD,
                self.environment == "development", amount,
            )),
            ("COMMIT", ()),
        ) as (_, (locked, (decided,), _)):
            account = self.checked_account(locked)
            cap = self.cap_with(account, [] if decided["extra_nusd"] is None else [decided])
            if decided["existing"]:
                # Never execute an operation a second time, including after a crash.
                raise PaidCallError("operation_already_admitted")
            if account["paused"]:
                raise PaidCallError("accounting_paused")
            if int(decided["committed"]) + amount > cap:
                raise PaidCallError("budget_exhausted", reset_at=reset_at(now))
            if not decided["admitted"]:
                raise PaidCallError("accounting_unavailable")

    def settle(
        self,
        operation_id: str,
        cost: int,
        usage: dict[str, int],
        response_id: str,
        model: str,
        elapsed_ms: int,
        now: datetime,
        reconciliation_reference: str | None = None,
    ) -> None:
        if cost < 0:
            raise PaidCallError("invalid_usage")
        # One round trip: the account lock, the row as it was, then a statement that
        # settles it and pauses the account on an overrun, then COMMIT. It changes nothing
        # when the account is invalid or the row is missing or already settled; the checks
        # below name those cases.
        with self.opened(
            self.lock_account(),
            ("SELECT * FROM brain_ops.operations WHERE environment = %s AND operation_id = %s",
             (self.environment, operation_id)),
            (SETTLE, (
                cost, Jsonb(usage), month_at(now), response_id, model, elapsed_ms,
                Jsonb({"reconciliation_reference": reconciliation_reference}
                      if reconciliation_reference else {}),
                self.environment, operation_id, self.environment, MONTHLY_CAP_NUSD,
                self.environment, cost,
            )),
            ("COMMIT", ()),
        ) as (_, (locked, rows, (outcome,), _)):
            self.checked_account(locked)
            if not rows:
                raise PaidCallError("operation_not_found")
            row = rows[0]
            if row["state"] == "settled":
                if (
                    row["cost_nusd"],
                    row["usage"],
                    row["provider_response_id"],
                    row["returned_model"],
                ) != (cost, usage, response_id, model):
                    raise PaidCallError("settlement_conflict")
                return
            if not outcome["settled"]:
                raise PaidCallError("accounting_unavailable")
        if cost > row["reserved_nusd"]:
            raise PaidCallError("accounting_bound_exceeded")

    def uncertain(self, operation_id: str, code: str, elapsed_ms: int) -> None:
        with self.opened(
            self.lock_account(),
            ("UPDATE brain_ops.operations SET state = 'uncertain', error_code = %s, "
             "elapsed_ms = %s, updated_at = clock_timestamp() "
             "WHERE environment = %s AND operation_id = %s AND state <> 'settled'",
             (code, elapsed_ms, self.environment, operation_id)),
        ) as (_, (account, _)):
            self.checked_account(account)  # Otherwise the update rolls back.

    def operations(self, request_id: str | None = None) -> list[dict[str, Any]]:
        """Operational report only: contains no prompts, student text, or raw responses."""
        with self.transaction() as conn:
            return conn.execute(
                "SELECT * FROM brain_ops.operations WHERE environment = %s "
                "AND (%s::text IS NULL OR request_id = %s) ORDER BY created_at",
                (self.environment, request_id, request_id),
            ).fetchall()

    def record_turn(self, request_id: str, summary: dict[str, Any]) -> None:
        with self.opened(
            ("INSERT INTO brain_ops.turns (environment, request_id, summary) "
             "VALUES (%s, %s, %s) "
             "ON CONFLICT (environment, request_id) DO UPDATE SET summary = EXCLUDED.summary",
             (self.environment, request_id, Jsonb(summary))),
            ("COMMIT", ()),
        ):
            pass


def main() -> None:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Inspect accounting without making paid calls")
    parser.add_argument("--request-id")
    args = parser.parse_args()
    environment = os.environ.get("BRAIN_ENVIRONMENT")
    if environment not in {"development", "production"}:
        raise ConfigurationError("Set BRAIN_ENVIRONMENT")
    from typing import cast

    ledger = PostgresLedger(os.environ["BRAIN_LEDGER_DATABASE_URL"], cast(Environment, environment))
    print(json.dumps(ledger.operations(args.request_id), default=str, indent=2))


if __name__ == "__main__":
    main()
