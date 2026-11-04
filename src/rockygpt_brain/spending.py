"""Durable admission against the existing brain_ops ledger; no prompts are stored."""

import uuid
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection, AsyncCursor, pq, sql
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from rockygpt_brain.settings import Environment

CAMPUS_TZ = ZoneInfo("America/New_York")
# The database closes connections that sit idle (Neon after a few minutes), and a closed one handed
# out by the pool fails the request that gets it. Idle connections are dropped well before that.
POOL_MAX_IDLE_SECONDS = 120.0
# A database that scaled to zero (Neon) needs a few seconds to wake. A request that arrives first
# waits for it instead of being refused; a database that is really down still refuses, just not
# at once. Nothing is admitted while it waits.
POOL_WAIT_SECONDS = 10.0
CONNECT_SECONDS = 8


class SpendingError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Reservation:
    operation_id: str
    amount_nusd: int


class Ledger(Protocol):
    async def ready(self) -> bool: ...
    async def reserve(
        self, request_id: str, amount_nusd: int, metadata: dict[str, Any], now: datetime,
    ) -> Reservation: ...
    async def settle(
        self, reservation: Reservation, cost_nusd: int, usage: dict[str, int],
        response_id: str, returned_model: str, now: datetime,
    ) -> None: ...
    async def uncertain(self, reservation: Reservation, code: str) -> None: ...
    async def release(self, reservation: Reservation, code: str, now: datetime) -> None: ...
    async def pause(self) -> None: ...


class PostgresLedger:
    """Each reservation serializes on its environment's account, across all workers.

    Old unsettled reservations count in every month until explicitly reconciled.
    All mutations, including settlements, take the same account lock. Application
    roles cannot raise caps or create dated development supplements.

    Every hold, charge or pause is one round trip: its statements go to the database together
    (a pipeline) with the commit, so the account lock is held only while the database runs them,
    never across the network. The lock is taken first and each later statement reads the ledger
    afresh (READ COMMITTED), so the cap is checked against every hold committed before it, as
    before. Each write also requires a valid cap (0 < cap <= 10,000,000,000 nUSD), so a broken
    account is never written to. (11-04 blind run: with the lock held across round trips, 42% of
    all answer time went to waiting on this ledger.)
    """

    def __init__(self, database_url: str, environment: Environment) -> None:
        self.environment = environment
        self._role = f"brain_{environment}"
        # Connections whose login passed _checked_login, for as long as they live.
        self._checked: weakref.WeakSet[AsyncConnection[Any]] = weakref.WeakSet()
        # `check` tests a connection as it is taken from the pool and swaps a closed one for a new
        # one, so the first request after a quiet spell is not refused. If the database really is
        # down the swap fails within `timeout` and the request is refused as before: never admitted.
        self._pool = AsyncConnectionPool(
            database_url, open=False, min_size=0, max_size=4, timeout=POOL_WAIT_SECONDS,
            check=AsyncConnectionPool.check_connection, max_idle=POOL_MAX_IDLE_SECONDS,
            kwargs={"autocommit": True, "connect_timeout": CONNECT_SECONDS},
        )

    async def open(self) -> None:
        await self._pool.open()

    async def close(self) -> None:
        await self._pool.close()

    async def _checked_login(self, connection: AsyncConnection[Any]) -> None:
        """SET ROLE enforces membership, but a superuser or a login with both environment roles
        would defeat isolation at configuration time. Those credentials are refused before either
        account is read. A connection keeps its login for life, so each one is checked once."""
        if connection in self._checked:
            return
        other_role = ("brain_production" if self.environment == "development"
                      else "brain_development")
        cursor = await connection.execute(
            "SELECT rolsuper OR rolcreaterole OR rolbypassrls "
            "OR pg_has_role(session_user, %s, 'MEMBER') "
            "FROM pg_catalog.pg_roles WHERE rolname = session_user",
            (other_role,),
        )
        privileges = await cursor.fetchone()
        if not privileges or privileges[0]:
            raise SpendingError("ledger_unavailable")
        self._checked.add(connection)

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[AsyncConnection[Any]]:
        """A pooled connection whose login was checked. Any database failure refuses."""
        try:
            async with self._pool.connection() as connection:
                await self._checked_login(connection)
                yield connection
        except SpendingError:
            raise
        except Exception as error:
            raise SpendingError("ledger_unavailable") from error

    @asynccontextmanager
    async def _transaction(self, connection: AsyncConnection[Any]) -> AsyncIterator[None]:
        """One transaction as the environment's role, sent as a pipeline: the statements run
        inside are queued and go to the database with BEGIN and COMMIT, in one round trip. Read
        their cursors after the block, when their results have arrived.

        BEGIN and COMMIT are plain statements because psycopg's transaction() waits for the
        database at its start and end in a pipeline (4 round trips, not 1). A failed statement
        makes the database skip the rest, the COMMIT too, and the block raises; the transaction
        it leaves open is rolled back here, so nothing of it is kept."""
        try:
            async with connection.pipeline():
                await connection.execute("BEGIN")
                await connection.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self._role)))
                await connection.execute("SET LOCAL statement_timeout = '3000ms'")
                yield
                await connection.execute("COMMIT")
        except BaseException:
            if connection.info.transaction_status != pq.TransactionStatus.IDLE:
                with suppress(Exception):
                    await connection.execute("ROLLBACK")
            raise

    async def _lock(self, connection: AsyncConnection[Any]) -> AsyncCursor[Any]:
        """Queues the account row read that holds the account lock until the commit."""
        return await connection.execute(
            "SELECT cap_nusd, paused FROM brain_ops.accounts WHERE environment = %s FOR UPDATE",
            (self.environment,),
        )

    @staticmethod
    async def _account(cursor: AsyncCursor[Any]) -> tuple[int, bool]:
        row = await cursor.fetchone()
        if not row or not 0 < row[0] <= 10_000_000_000:
            raise SpendingError("ledger_unavailable")
        return int(row[0]), bool(row[1])

    async def ready(self) -> bool:
        try:
            async with self._connection() as connection:
                async with self._transaction(connection):
                    cursor = await connection.execute(
                        "SELECT cap_nusd, paused FROM brain_ops.accounts WHERE environment = %s",
                        (self.environment,),
                    )
                    # Verify all required tables are accessible before serving traffic.
                    await connection.execute("SELECT 1 FROM brain_ops.operations LIMIT 0")
                    await connection.execute("SELECT 1 FROM brain_ops.monthly_allowances LIMIT 0")
                row = await cursor.fetchone()
            return bool(row and not row[1] and 0 < row[0] <= 10_000_000_000)
        except SpendingError:
            return False

    async def reserve(
        self, request_id: str, amount_nusd: int, metadata: dict[str, Any], now: datetime,
    ) -> Reservation:
        if amount_nusd <= 0:
            raise SpendingError("ledger_unavailable")
        month = now.astimezone(CAMPUS_TZ).date().replace(day=1)
        reservation = Reservation(str(uuid.uuid4()), amount_nusd)
        async with self._connection() as connection:
            async with self._transaction(connection):
                account = await self._lock(connection)
                # The hold is written only if the account is open and the month's committed spend
                # (settled costs, and every unsettled hold whatever its month) plus this hold fits
                # the cap and, in development, the month's dated supplement (production has none).
                # It runs once the lock is held, so it counts every hold committed before it.
                inserted = await connection.execute(
                    "INSERT INTO brain_ops.operations "
                    "(environment, operation_id, request_id, category, admitted_month, "
                    "reserved_nusd, metadata) "
                    "SELECT %(environment)s, %(operation)s, %(request)s, 'draft', %(month)s, "
                    "%(amount)s, %(metadata)s FROM brain_ops.accounts AS account "
                    "WHERE account.environment = %(environment)s AND NOT account.paused "
                    "AND account.cap_nusd > 0 AND account.cap_nusd <= 10000000000 "
                    "AND COALESCE((SELECT SUM(CASE WHEN state = 'settled' THEN cost_nusd "
                    "ELSE reserved_nusd END) FROM brain_ops.operations "
                    "WHERE environment = %(environment)s "
                    "AND (state <> 'settled' OR charged_month = %(month)s)), 0) + %(amount)s "
                    "<= account.cap_nusd + CASE WHEN %(development)s THEN "
                    "COALESCE((SELECT extra_nusd FROM brain_ops.monthly_allowances "
                    "WHERE environment = %(environment)s AND month = %(month)s), 0) ELSE 0 END",
                    {"environment": self.environment, "operation": reservation.operation_id,
                     "request": request_id, "month": month, "amount": amount_nusd,
                     "metadata": Jsonb(metadata),
                     "development": self.environment == "development"},
                )
            _, paused = await self._account(account)
            if paused:
                raise SpendingError("account_paused")
            if inserted.rowcount != 1:
                raise SpendingError("budget_exhausted")
        return reservation

    async def settle(
        self, reservation: Reservation, cost_nusd: int, usage: dict[str, int],
        response_id: str, returned_model: str, now: datetime,
    ) -> None:
        if cost_nusd < 0:
            raise SpendingError("ledger_unavailable")
        async with self._connection() as connection:
            async with self._transaction(connection):
                account = await self._lock(connection)
                if cost_nusd > reservation.amount_nusd:
                    # An overrun pauses further admissions in the same commit as its charge. The
                    # pause goes first, while the hold is still open: under the account lock the
                    # hold cannot change, so the account is paused exactly when the charge below
                    # is written.
                    await connection.execute(
                        "UPDATE brain_ops.accounts SET paused = true WHERE environment = %s "
                        "AND cap_nusd > 0 AND cap_nusd <= 10000000000 "
                        "AND EXISTS (SELECT 1 FROM brain_ops.operations WHERE environment = %s "
                        "AND operation_id = %s AND state <> 'settled')",
                        (self.environment, self.environment, reservation.operation_id),
                    )
                settled = await connection.execute(
                    "UPDATE brain_ops.operations SET state = 'settled', cost_nusd = %s, "
                    "charged_month = %s, usage = %s, provider_response_id = %s, "
                    "returned_model = %s, updated_at = clock_timestamp() "
                    "WHERE environment = %s AND operation_id = %s AND state <> 'settled' "
                    "AND EXISTS (SELECT 1 FROM brain_ops.accounts WHERE environment = %s "
                    "AND cap_nusd > 0 AND cap_nusd <= 10000000000)",
                    (cost_nusd, now.astimezone(CAMPUS_TZ).date().replace(day=1), Jsonb(usage),
                     response_id, returned_model, self.environment, reservation.operation_id,
                     self.environment),
                )
            await self._account(account)
            if settled.rowcount != 1:
                raise SpendingError("ledger_unavailable")

    async def uncertain(self, reservation: Reservation, code: str) -> None:
        async with self._connection() as connection:
            async with self._transaction(connection):
                account = await self._lock(connection)
                marked = await connection.execute(
                    "UPDATE brain_ops.operations SET state = 'uncertain', error_code = %s, "
                    "updated_at = clock_timestamp() WHERE environment = %s "
                    "AND operation_id = %s AND state <> 'settled' "
                    "AND EXISTS (SELECT 1 FROM brain_ops.accounts WHERE environment = %s "
                    "AND cap_nusd > 0 AND cap_nusd <= 10000000000)",
                    (code, self.environment, reservation.operation_id, self.environment),
                )
            await self._account(account)
            if marked.rowcount != 1:
                raise SpendingError("ledger_unavailable")

    async def release(self, reservation: Reservation, code: str, now: datetime) -> None:
        # Called only when the adapter proves no HTTP request was attempted.
        await self.settle(reservation, 0, {}, "", "", now)

    async def pause(self) -> None:
        async with self._connection() as connection:
            async with self._transaction(connection):
                account = await self._lock(connection)
                await connection.execute(
                    "UPDATE brain_ops.accounts SET paused = true WHERE environment = %s "
                    "AND cap_nusd > 0 AND cap_nusd <= 10000000000",
                    (self.environment,),
                )
            await self._account(account)
