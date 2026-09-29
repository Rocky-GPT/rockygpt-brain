"""The campus reader (campus.py): the timetable parsed strictly from a saved copy of the active
dataset, the cache around it, and the errors that must never show a connection string."""

import ast
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg_pool import PoolTimeout

from rockygpt_brain import campus
from rockygpt_brain.campus import (
    CampusReader,
    CampusUnavailable,
    Stop,
    Timetable,
    clock_minutes,
    parse,
    postgres_loader,
    stop_key,
)

FIXTURE = Path(__file__).parent / "fixtures" / "shuttle-timetable-20260929.json"


def rows() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(FIXTURE.read_text())["rows"]
    for row in data:
        row["collected_at"] = datetime.fromisoformat(row["collected_at"])
    return data


def timetable() -> Timetable:
    return parse(rows())


def test_the_saved_copy_is_four_timetables_and_51_trips() -> None:
    table = timetable()
    assert table.dataset_version == "dev-profiles-headings3-20260929"
    assert {(r.name, r.service_day, len(r.trips)) for r in table.routes} == {
        ("Weekday Roadrunner Express", "weekday", 12), ("Ramsey Route 17", "weekday", 18),
        ("Saturday Roadrunner Express", "saturday", 12), ("Sunday Roadrunner Express", "sunday", 9)}
    assert sum(len(route.trips) for route in table.routes) == 51
    assert (table.source.title, table.source.trust_tier, table.source.freshness_sla_hours) == (
        "Transportation Services", "official_primary", 168)
    assert table.collected_at == datetime.fromisoformat("2026-09-23T18:39:39.136-04:00")


def test_the_times_match_what_the_publisher_pins() -> None:
    table = timetable()
    weekday = {route.name: route for route in table.routes_on("weekday")}
    assert sorted(weekday) == ["Ramsey Route 17", "Weekday Roadrunner Express"]
    # Two routes leave at 7:00 AM: a tie the answer must show both of.
    assert [route.trips[0].departs for route in weekday.values()] == [420, 420]
    assert max(route.trips[-1].departs for route in weekday.values()) == 21 * 60 + 40
    train = weekday["Ramsey Route 17"]
    # critical_facts: first_departure 7:00 AM, last_dropoff 5:40 PM. This trip has no
    # return time, and its only stop is the train station.
    assert train.trips[-1].arrives is None
    station = Stop("Ramsey Rt 17 Train", "ramsey rt 17 train", 17 * 60 + 40)
    assert train.trips[-1].stops == (station,)
    assert [route.trips[0].departs for route in table.routes_on("sunday")] == [10 * 60]
    assert [route.trips[0].departs for route in table.routes_on("saturday")] == [9 * 60]


def test_every_trip_runs_forward_in_time_and_ramapo_is_never_a_stop() -> None:
    for route in timetable().routes:
        for trip in route.trips:
            times = [trip.departs, *(stop.minutes for stop in trip.stops)]
            assert times == sorted(times)
            assert all("ramapo" != stop.key for stop in trip.stops)


def test_the_stop_menu_is_exactly_the_seven_places_the_data_names() -> None:
    # A new spelling in the data changes Jev's options, so it must show up here first.
    assert [(c.key, c.place, c.visits) for c in timetable().stop_menu] == [
        ("ramsey rt 17 train", "Ramsey Rt 17 Train", 74),
        ("interstate plaza", "Interstate Plaza", 32),
        ("garden state plaza", "Garden State Plaza", 18),
        ("ramsey square", "Ramsey Square", 18),
        ("city md ramsey", "City MD Ramsey", 11),
        ("barnes & noble/fashion center", "Barnes & Noble/Fashion Center", 10),
        ("ramsey farmers market", "Ramsey Farmers Market", 4)]


@pytest.mark.parametrize(("text", "minutes"), [
    ("7:00 AM", 420), ("12:00 AM", 0), ("12:20 PM", 740), ("1:05 PM", 785),
    ("9:40 PM", 1300), ("11:59 PM", 1439)])
def test_a_published_time_is_minutes_since_midnight(text: str, minutes: int) -> None:
    assert clock_minutes(text) == minutes


@pytest.mark.parametrize("text", [
    "7:00", "07:00 AM", "13:00 PM", "0:30 AM", "7:60 AM", "7:00 am", "noon", "N/A", "", None, 7,
    "7:00 AM\n", " 7:00 AM"])
def test_anything_else_is_not_a_time(text: Any) -> None:
    with pytest.raises(CampusUnavailable) as error:
        clock_minutes(text)
    assert error.value.code == "campus_time"


def test_spellings_of_one_stop_are_one_key() -> None:
    assert (stop_key("Arrive Ramsey Rt 17 Train") == stop_key("Depart Ramsey Rt 17 Train")
            == stop_key("Ramsey Rt 17 Train") == "ramsey rt 17 train")
    assert stop_key("CITY MD Ramsey") == stop_key("City MD Ramsey")
    assert stop_key("Barnes & Noble/ Fashion Center") == "barnes & noble/fashion center"


def bad(change: Callable[[list[dict[str, Any]]], None]) -> str:
    data = rows()
    change(data)
    with pytest.raises(CampusUnavailable) as error:
        parse(data)
    return error.value.code


def set_stop_time(data: list[dict[str, Any]], value: Any) -> None:
    data[0]["stops"] = deepcopy(data[0]["stops"])
    data[0]["stops"][0]["time"] = value


@pytest.mark.parametrize(("expected", "change"), [
    ("campus_empty", lambda d: d.clear()),
    ("campus_count", lambda d: d.pop()),
    ("campus_count", lambda d: [row.update(verified_trips=None) for row in d]),
    ("campus_count", lambda d: [row.update(verified_trips=50) for row in d]),
    ("campus_time", lambda d: d[0].update(departure="7:00")),
    ("campus_time", lambda d: d[0].update(arrival="later")),
    ("campus_time", lambda d: set_stop_time(d, "25:00 AM")),
    ("campus_time", lambda d: set_stop_time(d, None)),
    # A stop before the trip leaves.
    ("campus_order", lambda d: set_stop_time(d, "1:00 AM")),
    # Two trips leave one route at the same minute.
    ("campus_duplicate", lambda d: d[1].update(departure=d[0]["departure"])),
    ("campus_shape", lambda d: d[0].update(service_day="holiday")),
    ("campus_shape", lambda d: d[0].update(service_day=None)),
    ("campus_shape", lambda d: d[0].update(stops="none")),
    ("campus_shape", lambda d: d[0].update(stops=[{"time": "7:10 AM"}])),
    ("campus_shape", lambda d: d[0].update(stops=[{"location": " ", "time": "7:10 AM"}])),
    ("campus_shape", lambda d: d[3].update(source_title="Another page")),
    ("campus_shape", lambda d: d[3].update(version="another-release")),
    ("campus_shape", lambda d: d[0].update(collected_at=d[0]["collected_at"].replace(tzinfo=None))),
    ("campus_shape", lambda d: [row.update(source_url="http://www.ramapo.edu/shuttle")
                                for row in d]),
    ("campus_shape", lambda d: d[0].pop("route_name")),
    ("campus_shape", lambda d: d[0].update(sequence=None)),
    # A trip that crosses midnight is refused on purpose: its day would need a rule.
    ("campus_order", lambda d: d[0].update(arrival="12:10 AM")),
    # Any date on any row: the answers say the timetable gives none.
    ("campus_dated", lambda d: d[4].update(trip_valid_from="2026-08-26")),
    ("campus_dated", lambda d: d[4].update(route_valid_until="2026-12-15")),
])
def test_anything_off_in_the_rows_fails_closed(
        expected: str, change: Callable[[list[dict[str, Any]]], None]) -> None:
    assert bad(change) == expected


def test_two_routes_may_leave_at_the_same_minute_but_one_route_may_not_repeat_itself() -> None:
    table = timetable()
    weekday = table.routes_on("weekday")
    assert len({route.trips[0].departs for route in weekday}) == 1  # both leave at 7:00 AM
    assert len(weekday) == 2
    data = rows()
    data[1]["departure"] = data[0]["departure"]  # the same route, the same minute
    with pytest.raises(CampusUnavailable) as error:
        parse(data)
    assert error.value.code == "campus_duplicate"


def test_the_count_is_the_publishers_own_not_a_number_the_reader_expects() -> None:
    # A later release may hold 49 trips or 55. The rows must match what it says it holds.
    data = rows()[:-2]
    for row in data:
        row["verified_trips"] = 49
    assert sum(len(route.trips) for route in parse(data).routes) == 49
    for row in data:
        row["verified_trips"] = 55
    with pytest.raises(CampusUnavailable) as error:
        parse(data)
    assert error.value.code == "campus_count"


def test_the_no_return_time_is_fine_but_only_for_n_a() -> None:
    data = rows()
    assert any(row["arrival"] == "N/A" for row in data)
    assert parse(data)  # "N/A" is the one arrival that isn't a time


def test_a_timetable_is_stale_only_past_the_sources_own_limit() -> None:
    table = timetable()
    limit = table.collected_at + timedelta(hours=168)
    assert not table.is_stale(limit)
    assert table.is_stale(limit + timedelta(seconds=1))
    assert not table.is_stale(table.collected_at + timedelta(hours=144))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Loads:
    """A loader that plays back results, each a Timetable to return or a code to fail with."""

    def __init__(self, *results: Timetable | str) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self) -> Timetable:
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, str):
            raise CampusUnavailable(result)
        return result


def reader(loads: Loads, clock: Clock) -> CampusReader:
    return CampusReader(loads, ttl=300, keep=900, retry=30, clock=clock)


def test_a_read_timetable_is_used_until_its_time_is_up() -> None:
    first, second, clock = timetable(), timetable(), Clock()
    loads = Loads(first, second)
    campus_reader = reader(loads, clock)
    assert campus_reader.timetable() is first
    clock.now += 299
    assert campus_reader.timetable() is first and loads.calls == 1
    clock.now += 2
    assert campus_reader.timetable() is second and loads.calls == 2


def test_when_a_refresh_fails_the_old_copy_serves_and_the_database_is_left_alone() -> None:
    old, new, clock = timetable(), timetable(), Clock()
    loads = Loads(old, "campus_unreachable", new)
    campus_reader = reader(loads, clock)
    campus_reader.timetable()
    clock.now += 400  # past the TTL: refresh, and it fails
    assert campus_reader.timetable() is old
    assert campus_reader.last_error == "campus_unreachable" and loads.calls == 2
    clock.now += 20  # inside the 30 s back-off: no new attempt
    assert campus_reader.timetable() is old and loads.calls == 2
    clock.now += 20  # back-off over: it tries again and recovers
    assert campus_reader.timetable() is new
    assert campus_reader.last_error is None and loads.calls == 3


def test_an_old_copy_is_kept_for_a_while_and_then_the_read_fails() -> None:
    clock = Clock()
    loads = Loads(timetable(), "campus_timeout", "campus_timeout")
    campus_reader = reader(loads, clock)
    campus_reader.timetable()
    clock.now += 800  # inside the 900 s keep window
    assert campus_reader.timetable()
    clock.now += 200  # 1,000 s old: past it
    with pytest.raises(CampusUnavailable) as error:
        campus_reader.timetable()
    assert error.value.code == "campus_timeout"


def test_a_first_read_that_fails_is_repeated_to_the_turns_behind_it_then_tried_again() -> None:
    clock = Clock()
    loads = Loads("campus_setup", timetable())
    campus_reader = reader(loads, clock)
    with pytest.raises(CampusUnavailable) as first:
        campus_reader.timetable()
    clock.now += 10  # inside the back-off: same failure, and the database is left alone
    with pytest.raises(CampusUnavailable) as again:
        campus_reader.timetable()
    assert (first.value.code, again.value.code, loads.calls) == ("campus_setup",) * 2 + (1,)
    clock.now += 25  # back-off over
    assert campus_reader.timetable() and loads.calls == 2


def test_a_copy_too_old_to_use_is_not_a_reason_to_hammer_the_database() -> None:
    clock = Clock()
    loads = Loads(timetable(), "campus_timeout", timetable())
    campus_reader = reader(loads, clock)
    campus_reader.timetable()
    clock.now += 1000  # past the 900 s keep window
    for _ in range(3):
        with pytest.raises(CampusUnavailable):
            campus_reader.timetable()
        clock.now += 5
    assert loads.calls == 2  # one failed refresh, then the back-off held
    clock.now += 30
    assert campus_reader.timetable() and loads.calls == 3


def test_turns_already_waiting_on_a_failing_refresh_share_its_failure_and_do_not_retry() -> None:
    calls = 0

    def load() -> Timetable:
        nonlocal calls
        calls += 1
        time.sleep(0.15)  # long enough for the other turns to arrive and queue behind it
        raise CampusUnavailable("campus_timeout")

    campus_reader = CampusReader(load, wait=5)
    codes: list[str] = []

    def turn() -> None:
        try:
            campus_reader.timetable()
        except CampusUnavailable as error:
            codes.append(error.code)

    threads = [threading.Thread(target=turn) for _ in range(5)]
    threads[0].start()
    time.sleep(0.03)
    for thread in threads[1:]:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == 1 and codes == ["campus_timeout"] * 5


def test_a_turn_with_no_copy_does_not_wait_forever_on_a_stuck_refresh() -> None:
    stuck = CampusReader(Loads(timetable()), wait=0.05)
    assert stuck._refreshing.acquire()  # another turn is reading, and never comes back
    try:
        with pytest.raises(CampusUnavailable) as error:
            stuck.timetable()
    finally:
        stuck._refreshing.release()
    assert error.value.code == "campus_timeout"


def test_a_turn_with_an_old_copy_never_waits_for_someone_elses_refresh() -> None:
    clock, old = Clock(), timetable()
    seen: list[Timetable] = []

    def load() -> Timetable:
        if not seen:
            seen.append(old)
            return old
        # Called from inside a refresh: another turn arrives while this one is reading.
        seen.append(campus_reader.timetable())
        return timetable()

    campus_reader = CampusReader(load, ttl=300, keep=900, retry=30, clock=clock)
    campus_reader.timetable()
    clock.now += 400
    campus_reader.timetable()
    assert seen[1] is old


def test_errors_and_reprs_never_show_the_connection_string() -> None:
    url = "postgresql://reader:hunter2secret@127.0.0.1:1/campus"
    load = postgres_loader(url, wait=0.5)
    try:
        with pytest.raises(CampusUnavailable) as error:
            load()
    finally:
        campus.close_campus_pools()
    assert error.value.code == "campus_unreachable"
    assert error.value.__cause__ is None and error.value.__suppress_context__
    campus_reader = CampusReader(load)
    for shown in (str(error.value), repr(error.value), repr(campus_reader), repr(load)):
        assert "hunter2secret" not in shown and "127.0.0.1" not in shown


def test_the_reader_reads_data_and_calls_no_model() -> None:
    source = (Path(campus.__file__)).read_text()
    imported = {alias.name.split(".")[0] for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Import) for alias in node.names}
    imported |= {(node.module or "").split(".")[0] for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.ImportFrom)}
    assert not imported & {"httpx", "openai", "requests"}
    assert not any(name.startswith("rockygpt_brain") for name in imported)


class FakeConnection:
    def __init__(self, attempt: "Attempt", statements: list[str]) -> None:
        self.attempt, self.statements = attempt, statements

    def transaction(self) -> Any:
        self.statements.append("BEGIN")
        return nullcontext()

    def execute(self, sql: str) -> Any:
        self.statements.append(" ".join(sql.split()))
        if " ".join(sql.split()) != " ".join(campus.QUERY.split()):
            return self
        if isinstance(self.attempt.query, Exception):
            raise self.attempt.query
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        assert not isinstance(self.attempt.query, Exception)
        return self.attempt.query


class Attempt:
    """One try at a read: the rows it returns, or the error it raises when it connects
    (`enter`) or when it runs the query."""

    def __init__(self, query: Exception | list[dict[str, Any]] | None = None,
                 enter: Exception | None = None) -> None:
        self.query = rows() if query is None else query
        self.enter = enter


class FakePool:
    def __init__(self, *attempts: Attempt) -> None:
        self.attempts = list(attempts)
        self.statements: list[str] = []

    @contextmanager
    def connection(self) -> Iterator[FakeConnection]:
        attempt = self.attempts.pop(0)
        if attempt.enter is not None:
            raise attempt.enter
        yield FakeConnection(attempt, self.statements)


def load_with(monkeypatch: pytest.MonkeyPatch, *attempts: Attempt) -> tuple[Timetable, FakePool]:
    pool = FakePool(*attempts)
    monkeypatch.setattr(campus, "pool_for", lambda url, wait: pool)
    return postgres_loader("postgresql://reader@127.0.0.1/campus")(), pool


def code_of(monkeypatch: pytest.MonkeyPatch, *attempts: Attempt) -> str:
    with pytest.raises(CampusUnavailable) as error:
        load_with(monkeypatch, *attempts)
    assert error.value.__cause__ is None and error.value.__suppress_context__
    return error.value.code


def test_a_read_is_one_read_only_transaction_with_a_time_limit_around_the_one_query(
        monkeypatch: pytest.MonkeyPatch) -> None:
    table, pool = load_with(monkeypatch, Attempt())
    assert sum(len(route.trips) for route in table.routes) == 51
    assert pool.statements == [
        "BEGIN", "SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = 2000",
        " ".join(campus.QUERY.split())]
    assert "dv.status = 'active'" in campus.QUERY  # pinned to the one active dataset


@pytest.mark.parametrize(("expected", "attempt"), [
    ("campus_timeout", Attempt(psycopg.errors.QueryCanceled("secret sql"))),
    ("campus_setup", Attempt(psycopg.errors.InsufficientPrivilege("secret"))),
    ("campus_setup", Attempt(psycopg.errors.UndefinedTable("secret"))),
    ("campus_setup", Attempt(psycopg.errors.UndefinedColumn("secret"))),
    ("campus_setup", Attempt(psycopg.errors.InvalidSchemaName("secret"))),
    ("campus_unreachable", Attempt(enter=PoolTimeout("secret host"))),
    ("campus_unreachable", Attempt(psycopg.errors.DataError("secret"))),
])
def test_each_way_a_read_fails_becomes_one_short_code(
        monkeypatch: pytest.MonkeyPatch, expected: str, attempt: Attempt) -> None:
    assert code_of(monkeypatch, attempt) == expected


def test_a_connection_that_died_while_idle_gets_one_fresh_try_and_no_more(
        monkeypatch: pytest.MonkeyPatch) -> None:
    dead = psycopg.OperationalError("server closed the connection: secret")
    table, pool = load_with(monkeypatch, Attempt(dead), Attempt())
    assert table.routes and pool.attempts == []
    assert code_of(monkeypatch, Attempt(dead), Attempt(dead)) == "campus_unreachable"


def test_a_pool_is_read_only_by_the_transaction_not_by_a_startup_option(
        monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[dict[str, Any]] = []

    class Recorder:
        def __init__(self, url: str, **kwargs: Any) -> None:
            made.append({"url": url, **kwargs})

        def close(self) -> None:
            pass

    monkeypatch.setattr(campus, "ConnectionPool", Recorder)
    campus.pool_for("postgresql://reader:pw@example.test/campus", 1.5)
    campus.close_campus_pools()
    (pool,) = made
    connection = pool["kwargs"]
    assert "options" not in connection  # a pooled endpoint can refuse startup options
    assert connection["prepare_threshold"] is None and connection["autocommit"] is True
    assert connection["keepalives"] == 1 and connection["tcp_user_timeout"] == 5000
    assert (pool["min_size"], pool["max_size"], pool["timeout"]) == (0, 4, 1.5)


def test_a_connection_string_libpq_cant_read_never_reaches_a_log(
        caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(CampusUnavailable) as error:
            postgres_loader("postgres://reader:hunter2secret@[::1/campus")()
    assert error.value.code == "campus_setup"
    assert "hunter2secret" not in caplog.text and "hunter2secret" not in repr(error.value)


@pytest.fixture
def campus_database() -> Iterator[str]:
    url = os.getenv("BRAIN_TEST_CAMPUS_DATABASE_URL")
    if not url:
        pytest.skip("Set BRAIN_TEST_CAMPUS_DATABASE_URL to a read-only login on a LOCAL copy")
    if urlsplit(url).hostname not in {"127.0.0.1", "localhost"} and "host=127.0.0.1" not in url:
        pytest.fail("Campus tests only run against a database on this machine")
    yield url
    campus.close_campus_pools()


def test_the_live_reader_agrees_with_a_direct_count(campus_database: str) -> None:
    table = postgres_loader(campus_database)()
    with psycopg.connect(campus_database, autocommit=True) as conn:
        (direct,) = conn.execute(
            "SELECT count(*) FROM rockygpt_v2.shuttle_trips t JOIN rockygpt_v2.dataset_versions dv"
            " ON dv.id = t.dataset_version_id WHERE dv.status = 'active'").fetchone() or (None,)
    assert sum(len(route.trips) for route in table.routes) == direct
    # It is the copy the reader was pinned to, not a sum over every dataset version.
    assert direct is not None and direct < 1000
