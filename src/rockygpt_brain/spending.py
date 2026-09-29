"""The spending cap: every paid call holds money in the ledger first and settles after.

The ledger is the brain_ops schema production already has (migrations/001 to 004). Each
environment has its own monthly cap, and development may have a dated supplement that
only an administrator adds. Amounts are USD nanodollars: $1 is 1,000,000,000.

A hold whose outcome is unknown ("uncertain") counts against its own month only. The old
Brain counted it against every later month too.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import date, datetime
from threading import Lock
from time import monotonic
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

CAMPUS_TIMEZONE = ZoneInfo("America/New_York")
# The most brain_ops.accounts.cap_nusd may be ($10), and the most one development month's
# supplement may add ($40, migrations/004). A ledger outside these is refused.
MONTHLY_CAP_NUSD = 10_000_000_000
SUPPLEMENT_CAP_NUSD = 40_000_000_000

Category = Literal["draft", "review", "routing"]
Environment = Literal["development", "production"]
Statement = tuple[str | sql.SQL | sql.Composed, Mapping[str, Any] | Sequence[Any] | None]
Connection = psycopg.Connection[dict[str, Any]]


class SpendingError(Exception):
    """A paid call that must not run, or whose accounting failed. `code` is safe to show."""

    def __init__(self, code: str, *, reset_at: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.reset_at = reset_at


def month_of(now: datetime) -> date:
    if now.tzinfo is None:
        raise ValueError("Spending needs a clock with a time zone")
    return now.astimezone(CAMPUS_TIMEZONE).date().replace(day=1)


def next_month(now: datetime) -> str:
    """When this month's allowance resets, in campus time."""
    month = month_of(now)
    year, number = (month.year + 1, 1) if month.month == 12 else (month.year, month.month + 1)
    return datetime(year, number, 1, tzinfo=CAMPUS_TIMEZONE).isoformat()


# Holds money in one statement, after the account lock, so it sees every hold committed
# before it. It inserts nothing unless every check passes; the caller names the refusal.
HOLD = """
WITH facts AS (
  SELECT account.cap_nusd, account.paused,
    (SELECT extra_nusd FROM brain_ops.monthly_allowances
      WHERE environment = %(environment)s AND month = %(month)s) AS extra_nusd,
    (SELECT COALESCE(SUM(CASE
        WHEN state = 'reserved' THEN reserved_nusd
        WHEN state = 'uncertain' AND admitted_month >= %(month)s THEN reserved_nusd
        WHEN state = 'settled' AND charged_month >= %(month)s THEN cost_nusd
        ELSE 0 END), 0)
      FROM brain_ops.operations WHERE environment = %(environment)s) AS committed
  FROM brain_ops.accounts AS account WHERE account.environment = %(environment)s
), held AS (
  INSERT INTO brain_ops.operations (environment, operation_id, request_id, category,
    admitted_month, reserved_nusd, metadata)
  SELECT %(environment)s, %(operation)s, %(request)s, %(category)s, %(month)s, %(amount)s,
    %(metadata)s || jsonb_build_object('monthly_cap_nusd', cap_nusd + COALESCE(extra_nusd, 0))
  FROM facts
  WHERE cap_nusd > 0 AND cap_nusd <= %(cap_ceiling)s
    AND COALESCE(extra_nusd, 0) BETWEEN 0 AND %(supplement_ceiling)s
    AND (COALESCE(extra_nusd, 0) = 0 OR %(environment)s = 'development')
    AND NOT paused
    AND committed + %(amount)s <= cap_nusd + COALESCE(extra_nusd, 0)
  RETURNING operation_id
)
SELECT facts.*, EXISTS (SELECT 1 FROM held) AS held FROM facts
"""

# Settles a hold with what the call really cost, in the month it settles. Costing more
# than was held still records the real cost, and pauses the account for a person to look.
SETTLE = """
WITH settled AS (
  UPDATE brain_ops.operations SET state = 'settled', cost_nusd = %(cost)s, usage = %(usage)s,
    charged_month = GREATEST(admitted_month, %(month)s), provider_response_id = %(response)s,
    returned_model = %(model)s, elapsed_ms = %(elapsed)s, updated_at = clock_timestamp()
  WHERE environment = %(environment)s AND operation_id = %(operation)s AND state <> 'settled'
  RETURNING reserved_nusd
), paused AS (
  UPDATE brain_ops.accounts SET paused = true
  WHERE environment = %(environment)s
    AND EXISTS (SELECT 1 FROM settled WHERE reserved_nusd < %(cost)s)
  RETURNING environment
)
SELECT (SELECT count(*) FROM settled) AS settled,
  (SELECT count(*) FROM paused) AS paused
"""

UNCERTAIN = """
UPDATE brain_ops.operations SET state = 'uncertain', error_code = %(code)s,
  elapsed_ms = %(elapsed)s, updated_at = clock_timestamp()
WHERE environment = %(environment)s AND operation_id = %(operation)s AND state = 'reserved'
"""

_POOLS: dict[str, ConnectionPool[Connection]] = {}
_POOLS_LOCK = Lock()


def pool_for(url: str) -> ConnectionPool[Connection]:
    """Connections kept open between turns. Opening one costs several round trips, and the
    development laptop is about 90 ms from the ledger."""
    with _POOLS_LOCK:
        pool = _POOLS.get(url)
        if pool is None:
            pool = ConnectionPool(
                url,
                connection_class=Connection,
                kwargs={"connect_timeout": 2, "row_factory": dict_row, "autocommit": True},
                min_size=0,
                max_size=8,
                max_idle=240,
                timeout=3,
                check=ConnectionPool.check_connection,
                name="ledger",
                open=True,
            )
            _POOLS[url] = pool
        return pool


def close_pools() -> None:
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


class PostgresLedger:
    def __init__(self, url: str, environment: Environment) -> None:
        self.url = url
        self.environment = environment

    def run(self, *statements: Statement) -> list[list[dict[str, Any]]]:
        """One transaction in one round trip: role, time limits, the account lock, then
        `statements`, then COMMIT. Returns the rows of each of `statements`."""
        setup: list[Statement] = [
            ("BEGIN", None),
            (sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(f"brain_{self.environment}")),
             None),
            ("SET LOCAL statement_timeout = '2000ms'", None),
            ("SET LOCAL lock_timeout = '1500ms'", None),
            ("SELECT * FROM brain_ops.accounts WHERE environment = %(environment)s FOR UPDATE",
             {"environment": self.environment}),
        ]
        try:
            with pool_for(self.url).connection() as conn:
                try:
                    results = batch(conn, [*setup, *statements, ("COMMIT", None)])
                except BaseException:
                    if not conn.closed and conn.info.transaction_status != (
                            psycopg.pq.TransactionStatus.IDLE):
                        with suppress(psycopg.Error):
                            conn.execute("ROLLBACK")
                    raise
        except psycopg.Error as error:
            raise SpendingError("accounting_unavailable") from error
        account = results[len(setup) - 1]
        if not account or not 0 < account[0]["cap_nusd"] <= MONTHLY_CAP_NUSD:
            raise SpendingError("accounting_unavailable")
        return results[len(setup):-1]

    def hold(self, request_id: str, category: Category, amount: int,
             metadata: dict[str, Any], now: datetime) -> str:
        """Holds `amount` for one paid call and returns the hold's id, or refuses."""
        if amount <= 0:
            raise SpendingError("price_unavailable")
        operation = str(uuid4())
        month = month_of(now)
        ((facts,),) = self.run((HOLD, {
            "environment": self.environment, "month": month, "operation": operation,
            "request": request_id, "category": category, "amount": amount,
            "metadata": Jsonb(metadata), "cap_ceiling": MONTHLY_CAP_NUSD,
            "supplement_ceiling": SUPPLEMENT_CAP_NUSD,
        }))
        if facts["held"]:
            return operation
        if facts["paused"]:
            raise SpendingError("accounting_paused")
        extra = facts["extra_nusd"] or 0
        if int(facts["committed"]) + amount > facts["cap_nusd"] + extra:
            raise SpendingError("budget_exhausted", reset_at=next_month(now))
        raise SpendingError("accounting_unavailable")

    def settle(self, operation: str, cost: int, usage: dict[str, int], response_id: str,
               model: str, elapsed_ms: int, now: datetime) -> None:
        if cost < 0:
            raise SpendingError("invalid_usage")
        ((outcome,),) = self.run((SETTLE, {
            "environment": self.environment, "operation": operation, "cost": cost,
            "usage": Jsonb(usage), "month": month_of(now), "response": response_id,
            "model": model, "elapsed": elapsed_ms,
        }))
        if not outcome["settled"]:
            raise SpendingError("operation_not_found")
        if outcome["paused"]:
            raise SpendingError("accounting_bound_exceeded")

    def uncertain(self, operation: str, code: str, elapsed_ms: int) -> None:
        self.run((UNCERTAIN, {"environment": self.environment, "operation": operation,
                              "code": code, "elapsed": elapsed_ms}))


def batch(conn: Connection, statements: list[Statement]) -> list[list[dict[str, Any]]]:
    """Runs statements as one message, binding their values on the client, and returns
    each one's rows. An error stops the rest."""
    with psycopg.ClientCursor(conn, row_factory=dict_row) as cursor:
        query = "; ".join(cursor.mogrify(text, values) for text, values in statements)
        cursor.execute(query)
        results: list[list[dict[str, Any]]] = []
        while True:
            results.append(cursor.fetchall() if cursor.description else [])
            if not cursor.nextset():
                return results


@dataclass
class Receipt:
    """What a paid call reports back: its cost once known, or that nothing was charged."""

    cost: int | None = None
    usage: dict[str, int] = field(default_factory=dict)
    response_id: str = ""
    model: str = ""


@contextmanager
def paid_call(ledger: PostgresLedger, request_id: str, category: Category, amount: int,
              now: datetime, metadata: dict[str, Any] | None = None) -> Iterator[Receipt]:
    """Holds `amount` before the call and settles after. A call that fails before its
    cost is known stays held as uncertain, counted against this month."""
    operation = ledger.hold(request_id, category, amount, metadata or {}, now)
    started = monotonic()
    receipt = Receipt()

    def elapsed() -> int:
        return round((monotonic() - started) * 1000)

    try:
        yield receipt
    except BaseException as error:
        if receipt.cost is None:
            code = error.code if isinstance(error, SpendingError) else type(error).__name__
            ledger.uncertain(operation, code[:100], elapsed())
        else:
            ledger.settle(operation, receipt.cost, receipt.usage, receipt.response_id,
                          receipt.model, elapsed(), now)
        raise
    if receipt.cost is None:
        ledger.uncertain(operation, "usage_unknown", elapsed())
        raise SpendingError("usage_unknown")
    ledger.settle(operation, receipt.cost, receipt.usage, receipt.response_id, receipt.model,
                  elapsed(), now)
