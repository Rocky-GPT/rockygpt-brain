"""The code-written shuttle answer (shuttle_answer.py) on the saved timetable, with a fixed clock:
what it says for every minute of the day, and that a wrong pick always shows in the words."""

import ast
import json
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain import shuttle_answer
from rockygpt_brain.campus import Timetable, parse
from rockygpt_brain.contract import ChatReply
from rockygpt_brain.shuttle_answer import (
    Operation,
    ShuttleAnswer,
    ShuttlePlan,
    age_limit,
    answer,
    service_day_of,
    time_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "shuttle-timetable-20260929.json"
NY = ZoneInfo("America/New_York")
TUESDAY = date(2026, 9, 29)
SATURDAY, SUNDAY = date(2026, 10, 3), date(2026, 10, 4)
NOW = datetime(2026, 9, 29, 14, 41, tzinfo=NY)
GSP, SQUARE = "garden state plaza", "ramsey square"
TIME = re.compile(r"\b(?:1[0-2]|[1-9]):[0-5][0-9] (?:AM|PM)\b")
COPIED = "Copied from Ramapo's published shuttle timetables on Sep 23."


def timetable() -> Timetable:
    rows = json.loads(FIXTURE.read_text())["rows"]
    for row in rows:
        row["collected_at"] = datetime.fromisoformat(row["collected_at"])
    return parse(rows)


TABLE = timetable()


def at(day: date, minutes: int) -> datetime:
    return datetime(day.year, day.month, day.day, minutes // 60, minutes % 60, tzinfo=NY)


def test_times_and_days_are_written_the_way_a_student_reads_them() -> None:
    assert [time_text(m) for m in (0, 60, 420, 720, 785, 1300, 1439)] == [
        "12:00 AM", "1:00 AM", "7:00 AM", "12:00 PM", "1:05 PM", "9:40 PM", "11:59 PM"]
    week = [date(2026, 9, 28) + timedelta(days=n) for n in range(7)]  # Monday to Sunday
    assert [service_day_of(d) for d in week] == ["weekday"] * 5 + ["saturday", "sunday"]


@pytest.mark.parametrize(("hours", "said"), [
    (1, "1 hour"), (6, "6 hours"), (47, "47 hours"), (48, "2 days"), (49, "3 days"),
    (168, "7 days"), (169, "8 days")])
def test_the_freshness_limit_is_said_the_way_a_person_says_it(hours: int, said: str) -> None:
    assert age_limit(hours) == said


def test_the_example_from_the_screenshot_at_2_41_pm() -> None:
    result = answer(ShuttlePlan("next", TUESDAY, GSP), TABLE, NOW)
    assert result.kind == "found" and not result.stale
    assert result.text == (
        "The published timetable lists the next shuttle from Ramapo with a stop at Garden State "
        "Plaza on Tuesday, Sep 29 (today) after 2:41 PM: the Weekday Roadrunner Express leaves "
        "at 4:40 PM and stops at Garden State Plaza at 5:25 PM.\n\n" + COPIED)


def test_two_routes_get_a_line_each_and_the_footer_is_its_own_paragraph() -> None:
    result = answer(ShuttlePlan("next", TUESDAY), TABLE, NOW)
    assert result.text == (
        "The published timetable lists the next shuttle from Ramapo to any stop on Tuesday, "
        "Sep 29 (today) after 2:41 PM, one per route:\n\n"
        "- Ramsey Route 17 leaves at 2:55 PM\n"
        "- Weekday Roadrunner Express leaves at 3:10 PM\n\n" + COPIED)
    tie = answer(ShuttlePlan("first", TUESDAY), TABLE, at(TUESDAY, 6 * 60 + 30))
    assert ("- Ramsey Route 17 leaves at 7:00 AM\n"
            "- Weekday Roadrunner Express leaves at 7:00 AM") in tie.text
    assert len(tie.citations) == 2


def test_a_weekend_has_one_route_and_one_line() -> None:
    result = answer(ShuttlePlan("last", SUNDAY), TABLE, NOW)
    assert "the Sunday Roadrunner Express leaves at 6:55 PM" in result.text
    assert "one per route" not in result.text and "Oct 4" in result.text


def test_a_route_with_nothing_left_is_not_dropped_without_a_word() -> None:
    result = answer(ShuttlePlan("next", TUESDAY), TABLE, at(TUESDAY, 18 * 60))
    assert "the Weekday Roadrunner Express leaves at 6:10 PM." in result.text
    assert "Ramsey Route 17 has no more trips after 6:00 PM (its last left at 5:30 PM)." in (
        result.text)
    with_stop = answer(ShuttlePlan("next", TUESDAY, "ramsey rt 17 train"), TABLE,
                       at(TUESDAY, 18 * 60))
    assert "has no more trips with a stop at Ramsey Rt 17 Train after 6:00 PM" in with_stop.text
    # When every route still has one, there is nothing to add.
    assert "no more trips" not in answer(ShuttlePlan("next", TUESDAY), TABLE, NOW).text


def test_next_on_another_day_is_the_first_of_that_day() -> None:
    result = answer(ShuttlePlan("next", TUESDAY + timedelta(days=1), GSP), TABLE, NOW)
    assert "the first shuttle" in result.text and "next shuttle" not in result.text
    assert "Wednesday, Sep 30 (tomorrow)" in result.text and "after" not in result.text
    assert "the Weekday Roadrunner Express leaves at 7:00 AM" in result.text


def test_the_first_or_last_of_today_that_already_left_says_so() -> None:
    result = answer(ShuttlePlan("first", TUESDAY), TABLE, NOW)
    assert result.text.count("left at 7:00 AM today") == 2
    assert "The time now is 2:41 PM." in result.text
    later = answer(ShuttlePlan("last", TUESDAY), TABLE, NOW)
    assert "left at" not in later.text and "The time now" not in later.text


def test_at_the_very_minute_a_trip_leaves_it_has_left_and_is_not_the_next() -> None:
    seven = at(TUESDAY, 7 * 60)
    first = answer(ShuttlePlan("first", TUESDAY), TABLE, seven)
    assert first.text.count("left at 7:00 AM today") == 2
    upcoming = answer(ShuttlePlan("next", TUESDAY), TABLE, seven).text.split("\n\n")[0]
    assert "leaves at 7:00 AM" not in upcoming and "left at" not in upcoming


def test_nothing_left_today_says_when_the_last_one_went() -> None:
    result = answer(ShuttlePlan("next", TUESDAY), TABLE, at(TUESDAY, 22 * 60 + 5))
    assert result.kind == "none_left"
    assert ("lists no more shuttles from Ramapo to any stop on Tuesday, Sep 29 (today) after "
            "10:05 PM. The last one left at 9:40 PM (Weekday Roadrunner Express).") in result.text
    assert len(result.citations) == 1


def test_a_stop_the_day_does_not_serve_says_which_days_do() -> None:
    result = answer(ShuttlePlan("next", SUNDAY, SQUARE), TABLE, NOW)
    assert result.kind == "stop_not_served"
    assert "no shuttle from Ramapo with a stop at Ramsey Square on Sunday, Oct 4" in result.text
    assert "does list shuttles with that stop on weekdays and Saturdays" in result.text
    market = answer(ShuttlePlan("next", TUESDAY, "ramsey farmers market"), TABLE, NOW)
    assert "with that stop on Sundays" in market.text


def test_a_stop_not_in_the_menu_still_names_itself_and_ends_in_a_plain_sentence() -> None:
    unknown = answer(ShuttlePlan("next", TUESDAY, "newark penn"), TABLE, NOW)
    assert unknown.kind == "stop_not_served" and "with a stop at newark penn" in unknown.text
    assert "lists no shuttle with that stop on any day" in unknown.text
    no_saturday = Timetable(TABLE.dataset_version, TABLE.source, TABLE.routes_on("weekday"),
                            TABLE.collected_at, TABLE.stop_menu)
    empty = answer(ShuttlePlan("first", SATURDAY), no_saturday, NOW)
    assert empty.kind == "no_service" and empty.citations == ()
    assert "lists no shuttle from Ramapo on Saturday, Oct 3" in empty.text


def test_an_old_copy_still_answers_and_says_it_may_be_outdated() -> None:
    fresh = answer(ShuttlePlan("next", TUESDAY), TABLE, NOW)
    assert "outdated" not in fresh.text and not fresh.stale
    assert {c.freshness for c in fresh.citations} == {"fresh"}
    old_now = TABLE.collected_at + timedelta(hours=168, minutes=1)
    old = answer(ShuttlePlan("first", old_now.date()), TABLE, old_now)
    assert old.kind == "found" and old.stale
    assert "That was more than 7 days ago, so it may be outdated." in old.text
    assert {c.freshness for c in old.citations} == {"stale"}


def test_the_copy_date_is_the_campus_date_even_when_the_database_says_utc() -> None:
    late_evening_eastern = datetime.fromisoformat("2026-09-24T02:30:00+00:00")  # Sep 23, 10:30 PM
    table = replace(TABLE, collected_at=late_evening_eastern)
    assert "on Sep 23." in answer(ShuttlePlan("first", TUESDAY), table, NOW).text


def test_a_past_or_far_off_day_says_the_timetable_gives_no_dates() -> None:
    for day in (date(2026, 12, 25), TUESDAY - timedelta(days=1)):
        text = answer(ShuttlePlan("first", day), TABLE, NOW).text
        assert f"The timetable gives no dates, so it may not apply on {day:%b} {day.day}." in text
    yesterday = answer(ShuttlePlan("first", TUESDAY - timedelta(days=1)), TABLE, NOW)
    assert "(yesterday)" in yesterday.text
    assert ", 2027" in answer(ShuttlePlan("first", date(2027, 9, 29)), TABLE, NOW).text
    near = answer(ShuttlePlan("first", TUESDAY + timedelta(days=3)), TABLE, NOW)
    assert "no dates" not in near.text


def test_the_source_is_not_claimed_to_be_one_page_holding_every_timetable() -> None:
    result = answer(ShuttlePlan("last", SUNDAY), TABLE, NOW)
    assert "Transportation Services" not in result.text and "page" not in result.text
    limits = result.citations[0].limitations or []
    assert any("main shuttle page on record" in line for line in limits)
    assert any("No dates are given" in line for line in limits)


def test_the_clock_can_be_in_any_zone_and_the_day_stays_the_campus_day() -> None:
    utc = datetime(2026, 9, 29, 18, 41, tzinfo=ZoneInfo("UTC"))  # 2:41 PM in New York
    assert answer(ShuttlePlan("next", TUESDAY, GSP), TABLE, utc) == answer(
        ShuttlePlan("next", TUESDAY, GSP), TABLE, NOW)
    just_after_midnight = answer(ShuttlePlan("first", SUNDAY), TABLE, at(SUNDAY, 10))
    assert "Sunday, Oct 4 (today)" in just_after_midnight.text
    assert "left at" not in just_after_midnight.text


def test_a_wrong_pick_always_changes_the_words() -> None:
    base = ShuttlePlan("next", TUESDAY, GSP)
    said = {
        "base": answer(base, TABLE, NOW).text,
        "any stop": answer(ShuttlePlan("next", TUESDAY), TABLE, NOW).text,
        "other stop": answer(ShuttlePlan("next", TUESDAY, "interstate plaza"), TABLE, NOW).text,
        "last": answer(ShuttlePlan("last", TUESDAY, GSP), TABLE, NOW).text,
        "first": answer(ShuttlePlan("first", TUESDAY, GSP), TABLE, NOW).text,
        "tomorrow": answer(ShuttlePlan("next", TUESDAY + timedelta(days=1), GSP), TABLE, NOW).text,
        "saturday": answer(ShuttlePlan("next", SATURDAY, GSP), TABLE, NOW).text,
    }
    assert len(set(said.values())) == len(said)
    assert "to any stop" in said["any stop"] and "with a stop at Garden State Plaza" in said["base"]
    assert "with a stop at Interstate Plaza" in said["other stop"]
    assert "the last shuttle" in said["last"] and "the first shuttle" in said["first"]
    assert "(tomorrow)" in said["tomorrow"] and "Saturday, Oct 3" in said["saturday"]
    for text in said.values():
        assert "The published timetable lists" in text and "from Ramapo" in text


def reference(op: Operation, day: date, stop: str | None, on_that_day: bool,
              now_minutes: int) -> dict[str, int]:
    """The route -> departure the question asks for, worked out the plain way."""
    found: dict[str, int] = {}
    for route in TABLE.routes_on(service_day_of(day)):
        trips = sorted((t for t in route.trips
                        if stop is None or any(s.key == stop for s in t.stops)),
                       key=lambda t: t.departs)
        if not trips:
            continue
        if op == "last":
            departs = trips[-1].departs
        elif op == "next" and on_that_day:
            later = [t.departs for t in trips if t.departs > now_minutes]
            if not later:
                continue
            departs = later[0]
        else:
            departs = trips[0].departs
        found[route.name] = departs
    return found


def fitting(day: date, stop: str | None) -> set[str]:
    """The routes that run that day and have at least one trip with the stop."""
    return {route.name for route in TABLE.routes_on(service_day_of(day))
            if any(stop is None or any(s.key == stop for s in t.stops) for t in route.trips)}


def check(result: ShuttleAnswer, op: Operation, day: date, now: datetime, stop: str | None,
          shown_ok: set[str]) -> None:
    on_that_day, minute = day == now.date(), now.hour * 60 + now.minute
    context = (op, day, stop, now)
    expected = reference(op, day, stop, on_that_day, minute)
    if expected:
        assert result.kind == "found", context
        for route in TABLE.routes:
            if route.name in expected:
                # It "left" exactly when it is today, not the next one, and not after now.
                left = op != "next" and on_that_day and expected[route.name] <= minute
                verb = "left" if left else "leaves"
                line = f"{route.name} {verb} at {time_text(expected[route.name])}"
                assert line + (" today" if left else "") in result.text, context
            else:
                assert f"{route.name} leaves at" not in result.text, context
                assert f"{route.name} left at" not in result.text, context
        if op == "next" and on_that_day:
            # A route with trips that fit but none left is named, not dropped.
            for name in fitting(day, stop) - set(expected):
                assert f"{name} has no more trips" in result.text, context
        else:
            assert "has no more trips" not in result.text, context
    else:
        assert result.kind in {"none_left", "stop_not_served"}, context
    # Every time it prints is a time in a record, or the clock itself.
    assert set(TIME.findall(result.text)) <= shown_ok, context
    assert "http" not in result.text and len(result.text) < 2000, context
    # The copy sentence is never inside a bullet: the last paragraph is not a list item.
    last_paragraph = result.text.split("\n\n")[-1]
    assert COPIED in last_paragraph and not last_paragraph.startswith("- "), context
    # An old copy says so, and only an old copy: past 7 days from when it was copied.
    assert result.stale == (now - TABLE.collected_at > timedelta(hours=168)), context
    assert ("may be outdated" in result.text) == result.stale, context


def record_times() -> set[str]:
    times = {time_text(t.departs) for r in TABLE.routes for t in r.trips}
    return times | {time_text(s.minutes) for r in TABLE.routes for t in r.trips for s in t.stops}


def test_every_minute_of_the_day_says_exactly_what_the_timetable_lists() -> None:
    listed = record_times()
    for day in (TUESDAY, SATURDAY, SUNDAY):
        for op in ("next", "first", "last"):
            for stop in (None, GSP, SQUARE):
                for minute in range(1440):
                    now = at(day, minute)
                    result = answer(ShuttlePlan(op, day, stop), TABLE, now)
                    check(result, op, day, now, stop, listed | {time_text(minute)})


def test_asking_about_another_day_never_depends_on_the_clock() -> None:
    listed = record_times() | {time_text(14 * 60 + 41)}
    for day in (TUESDAY + timedelta(days=1), SATURDAY, SUNDAY):
        for op in ("next", "first", "last"):
            for stop in (None, GSP, SQUARE):
                result = answer(ShuttlePlan(op, day, stop), TABLE, NOW)
                check(result, op, day, NOW, stop, listed)


def test_the_reply_it_builds_is_one_the_wire_accepts_and_its_only_link_is_the_sources() -> None:
    result = answer(ShuttlePlan("next", TUESDAY, GSP), TABLE, NOW)
    reply = ChatReply(answer=result.text, status="answered", citations=list(result.citations),
                      requestId="r")
    assert {c.url for c in reply.citations} == {TABLE.source.url}
    trip_ids = {t.id for r in TABLE.routes for t in r.trips}
    assert all(c.id in trip_ids and c.collection == "shuttle_trips" for c in reply.citations)
    assert all(c.valid_from is None and "No dates are given" in (c.limitations or [""])[0]
               for c in reply.citations)


def test_the_answer_reads_no_database_and_calls_no_model() -> None:
    tree = ast.parse(Path(shuttle_answer.__file__).read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not {i.split(".")[0] for i in imported} & {"httpx", "openai", "psycopg", "psycopg_pool"}
    assert not imported & {"rockygpt_brain.jev", "rockygpt_brain.spending"}
