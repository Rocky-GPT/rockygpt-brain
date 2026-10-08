"""Durable admission against the existing brain_ops ledger; no prompts are stored."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection, sql
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
    """

    def __init__(self, database_url: str, environment: Environment) -> None:
        self.environment = environment
        self._role = f"brain_{environment}"
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

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncConnection[Any]]:
        try:
            async with self._pool.connection() as connection, connection.transaction():
                # SET ROLE enforces membership, but a superuser or a login with
                # both environment roles would defeat isolation at configuration
                # time. Refuse those credentials before reading either account.
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
                await connection.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self._role))
                )
                await connection.execute("SET LOCAL statement_timeout = '3000ms'")
                yield connection
        except SpendingError:
            raise
        except Exception as error:
            raise SpendingError("ledger_unavailable") from error

    async def ready(self) -> bool:
        try:
            async with self._transaction() as connection:
                cursor = await connection.execute(
                    "SELECT cap_nusd, paused FROM brain_ops.accounts WHERE environment = %s",
                    (self.environment,),
                )
                row = await cursor.fetchone()
                # Verify all required tables are accessible before serving traffic.
                await connection.execute("SELECT 1 FROM brain_ops.operations LIMIT 0")
                await connection.execute("SELECT 1 FROM brain_ops.monthly_allowances LIMIT 0")
                return bool(row and not row[1] and 0 < row[0] <= 10_000_000_000)
        except SpendingError:
            return False

    async def _account(self, connection: AsyncConnection[Any]) -> tuple[int, bool]:
        cursor = await connection.execute(
            "SELECT cap_nusd, paused FROM brain_ops.accounts WHERE environment = %s FOR UPDATE",
            (self.environment,),
        )
        row = await cursor.fetchone()
        if not row or not 0 < row[0] <= 10_000_000_000:
            raise SpendingError("ledger_unavailable")
        return int(row[0]), bool(row[1])

    async def reserve(
        self, request_id: str, amount_nusd: int, metadata: dict[str, Any], now: datetime,
    ) -> Reservation:
        if amount_nusd <= 0:
            raise SpendingError("ledger_unavailable")
        month = now.astimezone(CAMPUS_TZ).date().replace(day=1)
        reservation = Reservation(str(uuid.uuid4()), amount_nusd)
        async with self._transaction() as connection:
            cap, paused = await self._account(connection)
            if paused:
                raise SpendingError("account_paused")
            if self.environment == "development":
                cursor = await connection.execute(
                    "SELECT extra_nusd FROM brain_ops.monthly_allowances "
                    "WHERE environment = %s AND month = %s", (self.environment, month),
                )
                extra = await cursor.fetchone()
                cap += int(extra[0]) if extra else 0
            cursor = await connection.execute(
                "SELECT COALESCE(SUM(CASE WHEN state = 'settled' THEN cost_nusd "
                "ELSE reserved_nusd END), 0) FROM brain_ops.operations "
                "WHERE environment = %s AND (state <> 'settled' OR charged_month = %s)",
                (self.environment, month),
            )
            row = await cursor.fetchone()
            if row is None or int(row[0]) + amount_nusd > cap:
                raise SpendingError("budget_exhausted")
            await connection.execute(
                "INSERT INTO brain_ops.operations "
                "(environment, operation_id, request_id, category, admitted_month, "
                "reserved_nusd, metadata) VALUES (%s, %s, %s, 'draft', %s, %s, %s)",
                (self.environment, reservation.operation_id, request_id, month,
                 amount_nusd, Jsonb(metadata)),
            )
        return reservation

    async def settle(
        self, reservation: Reservation, cost_nusd: int, usage: dict[str, int],
        response_id: str, returned_model: str, now: datetime,
    ) -> None:
        if cost_nusd < 0:
            raise SpendingError("ledger_unavailable")
        async with self._transaction() as connection:
            await self._account(connection)
            cursor = await connection.execute(
                "UPDATE brain_ops.operations SET state = 'settled', cost_nusd = %s, "
                "charged_month = %s, usage = %s, provider_response_id = %s, "
                "returned_model = %s, updated_at = clock_timestamp() "
                "WHERE environment = %s AND operation_id = %s AND state <> 'settled'",
                (cost_nusd, now.astimezone(CAMPUS_TZ).date().replace(day=1), Jsonb(usage),
                 response_id, returned_model, self.environment, reservation.operation_id),
            )
            if cursor.rowcount != 1:
                raise SpendingError("ledger_unavailable")
            if cost_nusd > reservation.amount_nusd:
                await connection.execute(
                    "UPDATE brain_ops.accounts SET paused = true WHERE environment = %s",
                    (self.environment,),
                )

    async def uncertain(self, reservation: Reservation, code: str) -> None:
        async with self._transaction() as connection:
            await self._account(connection)
            cursor = await connection.execute(
                "UPDATE brain_ops.operations SET state = 'uncertain', error_code = %s, "
                "updated_at = clock_timestamp() WHERE environment = %s "
                "AND operation_id = %s AND state <> 'settled'",
                (code, self.environment, reservation.operation_id),
            )
            if cursor.rowcount != 1:
                raise SpendingError("ledger_unavailable")

    async def release(self, reservation: Reservation, code: str, now: datetime) -> None:
        # Called only when the adapter proves no HTTP request was attempted.
        await self.settle(reservation, 0, {}, "", "", now)

    async def pause(self) -> None:
        async with self._transaction() as connection:
            await self._account(connection)
            await connection.execute(
                "UPDATE brain_ops.accounts SET paused = true WHERE environment = %s",
                (self.environment,),
            )
