"""Durable reservation accounting in a separate PostgreSQL schema. Amounts are USD nanodollars."""

import json
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from datetime import date, datetime
from threading import get_ident
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from rockygpt_brain.config import (
    DEVELOPMENT_SUPPLEMENT_CAP_NUSD,
    MONTHLY_CAP_NUSD,
    ConfigurationError,
    Environment,
)

CAMPUS_ZONE = ZoneInfo("America/New_York")
Category = Literal["draft", "review", "routing"]
Statement = tuple[str | sql.Composable, tuple[Any, ...]]
IDLE = psycopg.pq.TransactionStatus.IDLE


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
        self._session: psycopg.Connection[dict[str, Any]] | None = None
        self._session_thread: int | None = None

    @contextmanager
    def session(self) -> Iterator[None]:
        """Reuse one connection for a synchronous turn, never an open transaction."""
        if self._session is not None:
            raise PaidCallError("accounting_unavailable")
        try:
            with psycopg.connect(
                self.url, connect_timeout=2, row_factory=dict_row, autocommit=True
            ) as conn:
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
                else psycopg.connect(
                    self.url, connect_timeout=2, row_factory=dict_row, autocommit=True
                )
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
        # One round trip opens the transaction, takes the account lock and runs the
        # reads that must follow it; a second carries the hold and its COMMIT.
        with self.opened(
            self.lock_account(),
            self.allowance(now),
            ("SELECT operation_id FROM brain_ops.operations "
             "WHERE environment = %s AND operation_id = %s",
             (self.environment, operation_id)),
            ("SELECT COALESCE(SUM(CASE WHEN state <> 'settled' THEN reserved_nusd "
             "WHEN charged_month >= %s THEN cost_nusd ELSE 0 END), 0) AS committed "
             "FROM brain_ops.operations WHERE environment = %s",
             (month_at(now), self.environment)),
        ) as (conn, (locked, allowance, existing, totals)):
            account = self.checked_account(locked)
            cap = self.cap_with(account, allowance)
            if existing:
                # Never execute an operation a second time, including after a crash.
                raise PaidCallError("operation_already_admitted")
            if account["paused"]:
                raise PaidCallError("accounting_paused")
            if int(totals[0]["committed"]) + amount > cap:
                raise PaidCallError("budget_exhausted", reset_at=reset_at(now))
            self.batch(
                conn,
                ("INSERT INTO brain_ops.operations "
                 "(environment, operation_id, request_id, category, admitted_month, "
                 "reserved_nusd, metadata) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                 (self.environment, operation_id, request_id, category, month_at(now), amount,
                  Jsonb({**metadata, "monthly_cap_nusd": cap}))),
                ("COMMIT", ()),
            )

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
        overrun = False
        with self.opened(
            self.lock_account(),
            ("SELECT * FROM brain_ops.operations WHERE environment = %s AND operation_id = %s",
             (self.environment, operation_id)),
        ) as (conn, (locked, rows)):
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
            # Delayed usage is charged conservatively in the settlement month.
            # Its original admission month is retained for provider reconciliation.
            charged_month = max(row["admitted_month"], month_at(now))
            overrun = cost > row["reserved_nusd"]
            self.batch(
                conn,
                ("UPDATE brain_ops.operations SET state = 'settled', cost_nusd = %s, "
                 "usage = %s, charged_month = %s, provider_response_id = %s, "
                 "returned_model = %s, elapsed_ms = %s, metadata = metadata || %s, "
                 "updated_at = clock_timestamp() "
                 "WHERE environment = %s AND operation_id = %s",
                 (cost, Jsonb(usage), charged_month, response_id, model, elapsed_ms,
                  Jsonb({"reconciliation_reference": reconciliation_reference}
                        if reconciliation_reference else {}),
                  self.environment, operation_id)),
                # Record the actual liability even when the provider breaks the bound.
                *((("UPDATE brain_ops.accounts SET paused = true WHERE environment = %s",
                    (self.environment,)),) if overrun else ()),
                ("COMMIT", ()),
            )
        if overrun:
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
