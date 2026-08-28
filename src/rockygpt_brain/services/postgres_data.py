from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any
from urllib.parse import parse_qs, urlsplit

import asyncpg

from rockygpt_brain.services.data import DataPort, DataUnavailable

logger = logging.getLogger(__name__)


def _use_system_ca_store(database_url: str) -> ssl.SSLContext | bool | None:
    ssl_mode = parse_qs(urlsplit(database_url).query).get("sslmode", [""])[-1].casefold()
    if ssl_mode in {"verify-ca", "verify-full", "require"}:
        return ssl.create_default_context()
    if ssl_mode == "disable":
        return False
    if "ssl=true" in database_url.lower() or "sslmode=" in database_url.lower():
        return ssl.create_default_context()
    return None


class PostgresData:
    """Direct PostgreSQL reader implementing the DataPort protocol."""

    def __init__(
        self,
        database_url: str,
        *,
        fallback_http: DataPort | None = None,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
    ) -> None:
        if not database_url:
            raise ValueError("database_url must not be empty")
        self._database_url = database_url.strip().strip('"').strip("'")
        self._fallback_http = fallback_http
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool: asyncpg.Pool | None = None
        self._initialize_lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._pool is not None:
                return
            self._pool = await asyncpg.create_pool(
                dsn=self._database_url,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
                command_timeout=15,
                ssl=_use_system_ca_store(self._database_url),
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _ready_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            await self.initialize()
        if self._pool is None:
            raise RuntimeError("PostgresData did not initialize")
        return self._pool

    async def _active_dataset_id(self) -> str:
        pool = await self._ready_pool()
        row = await pool.fetchrow(
            """
            SELECT id::text
              FROM rockygpt_v2.dataset_versions
             WHERE is_active = true
             ORDER BY created_at DESC
             LIMIT 1
            """
        )
        if not row:
            raise DataUnavailable("No active dataset version found in database")
        return str(row["id"])

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.shuttle(query)
        raise NotImplementedError("shuttle query not yet implemented in PostgresData")

    async def transportation(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.shuttle(query)

    async def dining(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.dining(query)
        raise NotImplementedError("dining query not yet implemented in PostgresData")

    async def events(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.events(query)
        raise NotImplementedError("events query not yet implemented in PostgresData")

    async def campus_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.campus_hours(query)
        raise NotImplementedError("campus_hours query not yet implemented in PostgresData")

    async def dining_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.dining_hours(query)
        raise NotImplementedError("dining_hours query not yet implemented in PostgresData")

    async def courses(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.courses(query)
        raise NotImplementedError("courses query not yet implemented in PostgresData")

    async def course_subjects(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.course_subjects(query)
        raise NotImplementedError("course_subjects query not yet implemented in PostgresData")

    async def calendar(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.calendar(query)
        raise NotImplementedError("calendar query not yet implemented in PostgresData")

    async def clubs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.clubs(query)
        raise NotImplementedError("clubs query not yet implemented in PostgresData")

    async def directory(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.directory(query)
        raise NotImplementedError("directory query not yet implemented in PostgresData")

    async def locations(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.locations(query)
        raise NotImplementedError("locations query not yet implemented in PostgresData")

    async def programs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        if self._fallback_http is not None:
            return await self._fallback_http.programs(query)
        raise NotImplementedError("programs query not yet implemented in PostgresData")
