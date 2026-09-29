"""Campus data: the shuttle timetable of the active dataset, read once and kept a few minutes.

A published timetable is a schedule, not an entity, so it is read as a collection, and every
row keeps its record id and the time it was collected. Nothing here writes, calls a model or
reads the student's words. It reads one thing, the active dataset's shuttle rows, in one
SELECT, and fails closed: if anything about those rows is off, the reader raises
CampusUnavailable and never hands back a half-trusted timetable.

The dev copy holds 20 identical copies of the 51 trips, one per dataset version, so every read
is pinned to the version whose status is active. The count it checks against is the one the
publisher recorded for that release, never a number this file expects.

Refused on purpose, so they fail loudly and not quietly wrong: a trip that crosses midnight (its
day would need a rule the timetable doesn't give) and any row that carries dates (the answers
say the timetable gives none). The pool's own log lines may name the database host and port;
this module never puts a password, a query or student words in an error, repr or log line.
"""

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock
from time import monotonic
from typing import Any, Literal

import psycopg
from psycopg import Connection
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

ServiceDay = Literal["weekday", "saturday", "sunday"]
SERVICE_DAYS: tuple[ServiceDay, ...] = ("weekday", "saturday", "sunday")

# How long a read timetable is used before the next turn refreshes it, and how long an old
# one is kept when a refresh fails. Dan sets both (they are the cache's whole policy).
TTL_SECONDS = 300.0
KEEP_SECONDS = 900.0
# After a failed refresh, wait this long before trying again while an old copy is in use.
RETRY_SECONDS = 30.0
# The longest a turn with no usable copy waits for another turn's refresh.
LOAD_WAIT_SECONDS = 10.0

# One SELECT, one snapshot: every trip of the active dataset with its route and its source.
# The count the publisher verified rides along so a short read is caught.
QUERY = """
SELECT dv.version,
       (dv.quality_summary->'verifiedCounts'->>'shuttle_trips')::int AS verified_trips,
       s.title AS source_title, s.canonical_url AS source_url, s.trust_tier,
       s.freshness_sla_hours,
       r.id AS route_id, r.name AS route_name, r.service_day,
       r.valid_from AS route_valid_from, r.valid_until AS route_valid_until,
       t.id AS trip_id, t.sequence, t.departure, t.arrival, t.stops, t.collected_at,
       t.valid_from AS trip_valid_from, t.valid_until AS trip_valid_until
FROM rockygpt_v2.dataset_versions dv
JOIN rockygpt_v2.shuttle_routes r ON r.dataset_version_id = dv.id
JOIN rockygpt_v2.sources s ON s.id = r.source_id
JOIN rockygpt_v2.shuttle_trips t ON t.route_id = r.id AND t.dataset_version_id = dv.id
WHERE dv.status = 'active'
ORDER BY r.name, t.sequence
"""

CLOCK = re.compile(r"^([1-9]|1[0-2]):([0-5][0-9]) (AM|PM)$")
LEADING_ROLE = re.compile(r"^(arrive|depart)\s+", re.IGNORECASE)


class CampusUnavailable(Exception):
    """The timetable can't be read or isn't trustworthy. `code` says why, for the log and the
    dev metrics. It never carries the connection, a query or a driver message."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    trust_tier: str
    freshness_sla_hours: int


@dataclass(frozen=True)
class Stop:
    place: str  # as published, without a leading "Arrive " or "Depart "
    key: str  # folded, so "CITY MD Ramsey" and "City MD Ramsey" are one stop
    minutes: int  # since midnight, campus time


@dataclass(frozen=True)
class Trip:
    id: str
    sequence: int
    departs: int  # leaving Ramapo, minutes since midnight
    arrives: int | None  # back at Ramapo; None when the timetable lists none ("N/A")
    stops: tuple[Stop, ...]  # in time order; Ramapo itself is never one
    collected_at: datetime


@dataclass(frozen=True)
class Route:
    id: str
    name: str
    service_day: ServiceDay
    trips: tuple[Trip, ...]  # in the published order


@dataclass(frozen=True)
class StopChoice:
    key: str
    place: str  # the spelling the data uses most
    visits: int


@dataclass(frozen=True)
class Timetable:
    dataset_version: str
    source: Source
    routes: tuple[Route, ...]
    collected_at: datetime  # the oldest trip's: the timetable is only as fresh as that
    stop_menu: tuple[StopChoice, ...]  # every place a trip visits, most visited first

    def routes_on(self, service_day: ServiceDay) -> tuple[Route, ...]:
        return tuple(route for route in self.routes if route.service_day == service_day)

    def is_stale(self, now: datetime) -> bool:
        """Older than the source's own freshness limit. A stale timetable is still answered
        from, with a sentence saying so."""
        return now - self.collected_at > timedelta(hours=self.source.freshness_sla_hours)


def clock_minutes(text: Any) -> int:
    """"7:00 AM" as minutes since midnight. Anything else is not a time."""
    found = CLOCK.fullmatch(text) if isinstance(text, str) else None
    if found is None:
        raise CampusUnavailable("campus_time")
    hour, minute, half = int(found[1]) % 12, int(found[2]), found[3]
    return (hour + (12 if half == "PM" else 0)) * 60 + minute


def stop_key(place: str) -> str:
    """The stop a place name means: no leading role, one space around words, case folded."""
    text = LEADING_ROLE.sub("", place.strip())
    return " ".join(re.sub(r"\s*/\s*", "/", text).split()).casefold()


def shown(place: str) -> str:
    return " ".join(re.sub(r"\s*/\s*", "/", LEADING_ROLE.sub("", place.strip())).split())


def parse_stops(raw: Any) -> tuple[Stop, ...]:
    if not isinstance(raw, list):
        raise CampusUnavailable("campus_shape")
    stops: list[Stop] = []
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(item.get("location"), str) or (
                not item["location"].strip()):
            raise CampusUnavailable("campus_shape")
        stops.append(Stop(shown(item["location"]), stop_key(item["location"]),
                          clock_minutes(item.get("time"))))
    return tuple(stops)


def parse(rows: Sequence[Mapping[str, Any]]) -> Timetable:
    """The rows of QUERY as a Timetable, or CampusUnavailable if anything about them is off:
    no rows, a short read, an unreadable time, a trip whose times run backwards or cross
    midnight, two trips leaving one route at the same minute (two routes may), an unknown
    service day, a source that differs or isn't an https link, or any date on a row."""
    try:
        return build(rows)
    except (KeyError, TypeError, ValueError, AttributeError):
        # A row that doesn't have the shape the query promised.
        raise CampusUnavailable("campus_shape") from None


DATES = ("route_valid_from", "route_valid_until", "trip_valid_from", "trip_valid_until")


def build(rows: Sequence[Mapping[str, Any]]) -> Timetable:
    if not rows:
        raise CampusUnavailable("campus_empty")
    first = rows[0]
    verified = first["verified_trips"]
    if verified is None or int(verified) != len(rows):
        raise CampusUnavailable("campus_count")
    source = Source(str(first["source_title"]), str(first["source_url"]),
                    str(first["trust_tier"]), int(first["freshness_sla_hours"]))
    if not source.url.startswith("https://"):
        raise CampusUnavailable("campus_shape")
    by_route: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row["version"] != first["version"] or (
                (row["source_title"], row["source_url"]) != (first["source_title"],
                                                             first["source_url"])):
            raise CampusUnavailable("campus_shape")
        if any(row[name] is not None for name in DATES):
            raise CampusUnavailable("campus_dated")
        by_route.setdefault(str(row["route_id"]), []).append(row)

    routes: list[Route] = []
    visits: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    oldest: datetime | None = None
    for route_id, route_rows in by_route.items():
        service_day = route_rows[0]["service_day"]
        if service_day not in SERVICE_DAYS:
            raise CampusUnavailable("campus_shape")
        trips: list[Trip] = []
        leaving: set[int] = set()  # per route: two routes may leave at the same minute
        for row in route_rows:
            departs = clock_minutes(row["departure"])
            arrives = None if row["arrival"] == "N/A" else clock_minutes(row["arrival"])
            stops = parse_stops(row["stops"])
            times = [departs, *(stop.minutes for stop in stops)] + (
                [] if arrives is None else [arrives])
            if times != sorted(times):
                raise CampusUnavailable("campus_order")
            if departs in leaving:
                raise CampusUnavailable("campus_duplicate")
            leaving.add(departs)
            collected = row["collected_at"]
            if not isinstance(collected, datetime) or collected.tzinfo is None:
                raise CampusUnavailable("campus_shape")
            oldest = collected if oldest is None else min(oldest, collected)
            for stop, raw in zip(stops, row["stops"], strict=True):
                visits[stop.key] += 1
                spellings.setdefault(stop.key, Counter())[shown(raw["location"])] += 1
            trips.append(Trip(str(row["trip_id"]), int(row["sequence"]), departs, arrives,
                              stops, collected))
        routes.append(Route(route_id, str(route_rows[0]["route_name"]), service_day,
                            tuple(trips)))
    assert oldest is not None

    def spelling(key: str) -> str:
        # Spellings that differ only in case are one stop: show the plainer one, then the
        # one the data uses most.
        return min(spellings[key].items(),
                   key=lambda item: (sum(c.isupper() for c in item[0]), -item[1], item[0]))[0]

    menu = tuple(StopChoice(key, spelling(key), count)
                 for key, count in sorted(visits.items(), key=lambda item: (-item[1], item[0])))
    return Timetable(str(first["version"]), source, tuple(routes), oldest, menu)


# ---- Reading it from PostgreSQL ----

_POOLS: dict[str, ConnectionPool[Connection[Any]]] = {}
_POOLS_LOCK = Lock()

# Sent when a connection opens. Nothing here is a server "startup option", which a pooled
# endpoint can refuse; read-only and the statement limit are set in each read's transaction.
CONNECTION = {
    "connect_timeout": 3, "row_factory": dict_row, "autocommit": True,
    # Never keep server-side prepared statements: a pooler may hand the next read to another
    # session. And drop a connection that has gone silent instead of waiting on it forever.
    "prepare_threshold": None, "keepalives": 1, "keepalives_idle": 10,
    "keepalives_interval": 5, "keepalives_count": 2, "tcp_user_timeout": 5000,
}


def pool_for(url: str, wait: float) -> ConnectionPool[Connection[Any]]:
    """Connections kept open between reads. A read waits at most `wait` seconds for one."""
    with _POOLS_LOCK:
        pool = _POOLS.get(url)
        if pool is None:
            pool = ConnectionPool(url, kwargs=dict(CONNECTION), min_size=0, max_size=4,
                                  max_idle=240, timeout=wait, name="campus", open=True)
            _POOLS[url] = pool
        return pool


def close_campus_pools() -> None:
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


def postgres_loader(url: str, wait: float = 3.0) -> Callable[[], Timetable]:
    """A function that reads the active timetable. The connection string stays in this
    closure: no error, repr or log line built from what it raises can show it."""
    def load() -> Timetable:
        try:
            # A string libpq can't read is logged in full, password included, by the pool.
            conninfo_to_dict(url)
        except psycopg.Error:
            raise CampusUnavailable("campus_setup") from None
        rows: list[dict[str, Any]] | None = None
        for attempt in (1, 2):
            try:
                with pool_for(url, wait).connection() as conn, conn.transaction():
                    conn.execute("SET TRANSACTION READ ONLY")
                    conn.execute("SET LOCAL statement_timeout = 2000")
                    rows = conn.execute(QUERY).fetchall()
                break
            except psycopg.errors.QueryCanceled:
                raise CampusUnavailable("campus_timeout") from None
            except (psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedTable,
                    psycopg.errors.UndefinedColumn, psycopg.errors.InvalidSchemaName):
                raise CampusUnavailable("campus_setup") from None
            except PoolTimeout:
                raise CampusUnavailable("campus_unreachable") from None
            except psycopg.OperationalError:
                # A kept connection can have died while idle; one fresh try is enough.
                if attempt == 2:
                    raise CampusUnavailable("campus_unreachable") from None
            except psycopg.Error:
                # Whatever else the driver raises, its message may hold the connection.
                raise CampusUnavailable("campus_unreachable") from None
        assert rows is not None
        return parse(rows)
    return load


@dataclass
class CampusReader:
    """The timetable, read at most once every `ttl` seconds. If a refresh fails, the old copy
    is used until it is `keep` seconds old, and nobody asks the database again for `retry`
    seconds. With no usable copy a turn waits at most `wait` seconds for someone else's
    refresh, and a failed refresh is repeated to the turns behind it, not retried by each."""

    load: Callable[[], Timetable] = field(repr=False)
    ttl: float = TTL_SECONDS
    keep: float = KEEP_SECONDS
    retry: float = RETRY_SECONDS
    wait: float = LOAD_WAIT_SECONDS
    clock: Callable[[], float] = field(default=monotonic, repr=False)
    last_error: str | None = None
    _copy: Timetable | None = field(default=None, repr=False)
    _read_at: float = field(default=0.0, repr=False)
    _retry_at: float = field(default=0.0, repr=False)
    _refreshing: Lock = field(default_factory=Lock, repr=False)

    def usable(self) -> Timetable | None:
        """The copy, if it is young enough to answer from when a refresh can't."""
        if self._copy is not None and self.clock() - self._read_at < self.keep:
            return self._copy
        return None

    def timetable(self) -> Timetable:
        if self._copy is not None and self.clock() - self._read_at < self.ttl:
            return self._copy
        if (fallback := self._after_failure()) is not None:
            return fallback
        # A turn with a usable copy never waits for someone else's refresh; the rest wait, but
        # not forever.
        had_copy = self.usable() is not None
        got = (self._refreshing.acquire(blocking=False) if had_copy
               else self._refreshing.acquire(timeout=self.wait))
        if not got:
            if (copy := self.usable()) is not None:
                return copy
            raise CampusUnavailable("campus_timeout")
        try:
            if self._copy is not None and self.clock() - self._read_at < self.ttl:
                return self._copy  # another turn just refreshed it
            if (fallback := self._after_failure()) is not None:
                return fallback  # another turn just failed: don't ask the database again
            try:
                fresh = self.load()
            except CampusUnavailable as error:
                self.last_error = error.code
                self._retry_at = self.clock() + self.retry
                if (copy := self.usable()) is not None:
                    return copy
                raise
            self._copy, self._read_at, self.last_error = fresh, self.clock(), None
            return fresh
        finally:
            self._refreshing.release()

    def _after_failure(self) -> Timetable | None:
        """Within `retry` seconds of a failed refresh: the usable copy, or the same failure."""
        if self.clock() >= self._retry_at:
            return None
        if (copy := self.usable()) is not None:
            return copy
        raise CampusUnavailable(self.last_error or "campus_unreachable")
