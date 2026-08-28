from __future__ import annotations

import asyncio
import logging
import ssl
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import asyncpg

from rockygpt_brain.services.data import DataPort, DataUnavailable
from rockygpt_brain.services.shuttle_query import (
    CAMPUS_TIME_ZONE,
    DatedTrip,
    ShuttleTripRecord,
    campus_date,
    execute_shuttle_query,
    previous_date,
    service_day_for_date,
)

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
             WHERE status = 'active'
             ORDER BY activated_at DESC
             LIMIT 1
            """
        )
        if not row:
            raise DataUnavailable("No active dataset version found in database")
        return str(row["id"])

    async def list_shuttle_trips(
        self, dataset_id: str, service_day: str
    ) -> list[ShuttleTripRecord]:
        pool = await self._ready_pool()
        rows = await pool.fetch(
            """
            SELECT r.name AS route, t.departure, t.arrival, t.stops,
                   s.id::text AS source_id
              FROM rockygpt_v2.shuttle_trips t
              JOIN rockygpt_v2.shuttle_routes r ON r.id = t.route_id
              JOIN rockygpt_v2.sources s ON s.id = t.source_id
             WHERE t.dataset_version_id = $1::uuid
               AND (
                 r.service_day = $2::text
                 OR (
                   r.service_day IS NULL
                   AND CASE $2::text
                     WHEN 'weekday' THEN lower(r.name) NOT LIKE '%saturday%'
                                       AND lower(r.name) NOT LIKE '%sunday%'
                     WHEN 'saturday' THEN lower(r.name) LIKE '%saturday%'
                     WHEN 'sunday' THEN lower(r.name) LIKE '%sunday%'
                     ELSE false
                   END
                 )
               )
             ORDER BY t.sequence, r.name
            """,
            dataset_id,
            service_day,
        )
        records: list[ShuttleTripRecord] = []
        for row in rows:
            raw_stops = row["stops"]
            stops_list: list[dict[str, str]] = []
            if isinstance(raw_stops, str):
                import json

                try:
                    loaded = json.loads(raw_stops)
                    if isinstance(loaded, list):
                        raw_stops = loaded
                except Exception:
                    raw_stops = []
            if isinstance(raw_stops, list):
                for s in raw_stops:
                    if isinstance(s, dict) and "location" in s and "time" in s:
                        stops_list.append({"location": str(s["location"]), "time": str(s["time"])})
            records.append(
                ShuttleTripRecord(
                    route=str(row["route"]),
                    departure=str(row["departure"]),
                    arrival=str(row["arrival"]),
                    stops=stops_list,
                    source_id=str(row["source_id"]),
                )
            )
        return records

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        dataset_id = await self._active_dataset_id()
        as_of_str = query.get("asOf")
        if as_of_str:
            try:
                as_of = datetime.fromisoformat(as_of_str)
            except Exception:
                as_of = datetime.now(ZoneInfo(CAMPUS_TIME_ZONE))
        else:
            as_of = datetime.now(ZoneInfo(CAMPUS_TIME_ZONE))

        service_date = query.get("serviceDate") or campus_date(as_of)
        time_scope = query.get("timeScope", "remaining")
        if service_date == campus_date(as_of) and time_scope in ("at_time", "remaining"):
            dates_considered = [service_date, previous_date(service_date)]
        else:
            dates_considered = [service_date]

        all_trips: list[DatedTrip] = []
        for s_date in dates_considered:
            s_day = service_day_for_date(s_date)
            trips = await self.list_shuttle_trips(dataset_id, s_day)
            for t in trips:
                all_trips.append(DatedTrip(trip=t, service_date=s_date, service_day=s_day))

        return execute_shuttle_query(all_trips, query, as_of)

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
