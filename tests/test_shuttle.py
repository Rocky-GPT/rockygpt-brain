"""The shuttle answer is code: next/first/last by the campus clock, against the real timetable."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from rockygpt_brain.shuttle import (
    clock,
    departures,
    minutes,
    said_minutes,
    service_day,
    stop_key,
    stops_served,
    trips_of,
)

FIXTURE = Path(__file__).parent / "fixtures" / "shuttle-timetable-20260929.json"
ROWS = json.loads(FIXTURE.read_text())["rows"]


def routes() -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for row in ROWS:
        route = by_name.setdefault(row["route_name"], {
            "route": row["route_name"], "service_day": row["service_day"], "trips": []})
        route["trips"].append({"sequence": row["sequence"], "departure": row["departure"],
                               "arrival": row["arrival"], "stops": row["stops"]})
    return list(by_name.values())


TUESDAY, SATURDAY, SUNDAY = date(2026, 10, 13), date(2026, 10, 17), date(2026, 10, 18)


def test_a_date_is_a_class_of_service_day() -> None:
    assert [service_day(date(2026, 10, d)) for d in range(12, 19)] == [
        "weekday"] * 5 + ["saturday", "sunday"]


def test_clock_text_reads_and_writes_the_way_the_timetable_does() -> None:
    assert minutes("7:00 AM") == 420 and minutes("12:00 AM") == 0 and minutes("12:30 PM") == 750
    assert minutes("11:00 PM") == 1380 and minutes("N/A") is None and minutes("25:00") is None
    assert clock(420) == "7:00 AM" and clock(0) == "12:00 AM" and clock(750) == "12:30 PM"


def test_stop_names_fold_prefix_case_and_spacing() -> None:
    assert stop_key("Arrive Ramsey Rt 17 Train") == stop_key("Depart Ramsey Rt 17 Train")
    assert stop_key("CITY MD Ramsey") == stop_key("City MD Ramsey")
    assert stop_key("Barnes & Noble/ Fashion Center") == stop_key("Barnes & Noble/Fashion Center")


def test_the_published_stops_are_the_seven_places() -> None:
    names = {stop_key(n) for n in stops_served(routes())}
    assert names == {stop_key(n) for n in (
        "Ramsey Rt 17 Train", "Interstate Plaza", "Garden State Plaza", "Ramsey Square",
        "City MD Ramsey", "Barnes & Noble/Fashion Center", "Ramsey Farmers Market")}


def test_every_published_trip_is_readable() -> None:
    total = sum(len(trips_of(routes(), s)) for s in ("weekday", "saturday", "sunday"))
    assert total == len(ROWS) == 51


@pytest.mark.parametrize("day,stop", [
    (TUESDAY, None), (TUESDAY, "Ramsey Rt 17 Train"), (TUESDAY, "Garden State Plaza"),
    (SATURDAY, None), (SATURDAY, "Interstate Plaza"), (SUNDAY, "Ramsey Rt 17 Train"),
])
def test_next_matches_a_plain_reference_at_every_minute_of_the_day(
        day: date, stop: str | None) -> None:
    """The reference is the obvious loop: the earliest departure strictly after the minute."""
    table = routes()
    key = stop_key(stop) if stop else None
    service = service_day(day)
    reference = sorted(
        (minutes(row["departure"]), row["route_name"]) for row in ROWS
        if row["service_day"] == service and (
            key is None or any(stop_key(s["location"]) == key for s in row["stops"])))
    for now in range(0, 24 * 60):
        got = departures(table, day, now, "next", stop)
        later = [item for item in reference if item[0] > now]
        shown = [(minutes(t["leaves_campus"]), t["route"]) for t in got.get("trips", [])]
        assert shown == later[:2], (day, stop, now)
        assert got.get("none_left", False) is (not later)


def test_at_the_minute_a_shuttle_leaves_it_has_left() -> None:
    got = departures(routes(), TUESDAY, minutes("7:00 AM"), "next", None)
    assert [t["leaves_campus"] for t in got["trips"]][0] != "7:00 AM"
    before = departures(routes(), TUESDAY, minutes("7:00 AM") - 1, "next", None)
    assert before["trips"][0]["leaves_campus"] == "7:00 AM"


def test_after_the_last_trip_the_next_service_day_is_named_with_its_date() -> None:
    got = departures(routes(), TUESDAY, minutes("11:30 PM"), "next", None)
    assert got["none_left"] is True and "trips" in got and got["trips"] == []
    assert got["last_left_at"] == "9:40 PM"
    assert got["next_service"]["date"] == "2026-10-14"
    assert got["next_service"]["trips"][0]["leaves_campus"] == "7:00 AM"
    # Saturday night rolls to Sunday, whose timetable is the Sunday one.
    sat = departures(routes(), SATURDAY, minutes("11:59 PM"), "next", None)
    assert sat["next_service"]["date"] == "2026-10-18"
    assert sat["next_service"]["trips"][0]["leaves_campus"] == "10:00 AM"


def test_first_and_last_of_the_day_say_whether_they_have_left() -> None:
    first = departures(routes(), TUESDAY, minutes("6:00 AM"), "first", None)
    assert [t["leaves_campus"] for t in first["trips"]] == ["7:00 AM", "7:00 AM"]
    assert {t["route"] for t in first["trips"]} == {"Ramsey Route 17", "Weekday Roadrunner Express"}
    assert first["already_left"] is False
    gone = departures(routes(), TUESDAY, minutes("1:00 PM"), "first", None)
    assert gone["already_left"] is True
    last = departures(routes(), TUESDAY, minutes("1:00 PM"), "last", None)
    assert [t["leaves_campus"] for t in last["trips"]] == ["9:40 PM"]
    assert last["already_left"] is False


def test_another_date_has_nothing_that_already_left() -> None:
    other = departures(routes(), SATURDAY, None, "first", None)
    assert "already_left" not in other and other["trips"][0]["leaves_campus"] == "9:00 AM"


def test_a_stop_a_day_does_not_serve_is_no_service_for_it_not_an_empty_list() -> None:
    # Sunday's trips never call at Barnes & Noble; only Sunday's call at the Farmers Market.
    got = departures(routes(), SUNDAY, minutes("8:00 AM"), "next", "Barnes & Noble/Fashion Center")
    assert got["service"] == "none" and "trips" not in got
    # It still names the stop, and says on which kinds of day the stop is served.
    assert got["stop"] == "Barnes & Noble/Fashion Center"
    assert got["served_on"] == ["weekdays", "Saturdays"]
    market = departures(routes(), TUESDAY, minutes("8:00 AM"), "next", "Ramsey Farmers Market")
    assert market["service"] == "none"
    sunday = departures(routes(), SUNDAY, minutes("8:00 AM"), "next", "Ramsey Farmers Market")
    assert sunday["trips"]


def test_a_trip_shows_where_it_is_reached_and_where_it_is_left_going_back() -> None:
    got = departures(routes(), TUESDAY, minutes("6:00 AM"), "first", "Ramsey Rt 17 Train")
    express = next(t for t in got["trips"] if t["route"] == "Ramsey Route 17")
    assert express["leaves_campus"] == "7:00 AM" and express["reached_at"] == "7:10 AM"
    assert express["stop"] == "Ramsey Rt 17 Train" and express["back_on_campus"] == "7:20 AM"


def test_a_time_that_runs_backwards_is_not_read() -> None:
    bad = [{"route": "R", "service_day": "weekday", "trips": [
        {"departure": "9:00 AM", "arrival": "9:30 AM",
         "stops": [{"location": "A", "time": "9:20 AM"}, {"location": "B", "time": "9:10 AM"}]},
        {"departure": "10:00 AM", "arrival": "10:30 AM",
         "stops": [{"location": "A", "time": "10:10 AM"}]},
    ]}]
    assert [t["leaves"] for t in trips_of(bad, "weekday")] == [600]


def test_a_time_as_a_student_says_it_is_read() -> None:
    assert said_minutes("6pm") == 1080 and said_minutes("6:30 PM") == 1110
    assert said_minutes("12am") == 0 and said_minutes("12:15 p.m.") == 735
    assert said_minutes("18:30") == 1110 and said_minutes("6:50 AM") == 410
    assert said_minutes("evening") is None and said_minutes("13pm") is None
    assert said_minutes(None) is None


def test_after_keeps_only_trips_that_leave_campus_after_the_time() -> None:
    table = routes()
    for stop in (None, "Interstate Plaza", "Garden State Plaza"):
        key = stop_key(stop) if stop else None
        for now, after in ((minutes("4:00 AM"), minutes("6:00 PM")),
                           (minutes("7:00 PM"), minutes("6:00 PM")),
                           (minutes("10:00 AM"), minutes("5:00 PM"))):
            reference = sorted(
                (minutes(row["departure"]), row["route_name"]) for row in ROWS
                if row["service_day"] == "weekday" and (
                    key is None or any(stop_key(s["location"]) == key for s in row["stops"]))
                and minutes(row["departure"]) > max(now, after))
            got = departures(table, TUESDAY, now, "next", stop, after=after)
            shown = [(minutes(t["leaves_campus"]), t["route"]) for t in got["trips"]]
            assert shown == reference[:2], (stop, now, after)
            assert got["after"] == clock(after)


def test_nothing_after_the_time_names_the_days_last_and_the_next_days_first() -> None:
    got = departures(routes(), TUESDAY, minutes("4:00 AM"), "next", None, after=minutes("11:00 PM"))
    assert got["none_left"] is True and got["trips"] == []
    assert got["last_left_at"] == "9:40 PM" and got["next_service"]["date"] == "2026-10-14"


def test_by_answers_with_the_latest_trips_that_reach_the_stop_in_time() -> None:
    table = routes()
    got = departures(table, TUESDAY, minutes("4:00 AM"), "next", "Ramsey Rt 17 Train",
                     by=minutes("7:36 AM"))
    # The 7:00 trip reaches the train at 7:10 and the 7:25 at 7:35; the 7:50 is too late.
    assert [t["reached_at"] for t in got["trips"]] == ["7:10 AM", "7:35 AM"]
    assert got["by"] == "7:36 AM" and got["none_by"] is False
    early = departures(table, TUESDAY, minutes("4:00 AM"), "next", "Ramsey Rt 17 Train",
                       by=minutes("6:50 AM"))
    assert early["none_by"] is True and early["trips"] == []
    assert early["earliest_reaches_stop"] == "7:10 AM"
    # A trip that has already left today cannot be taken.
    late = departures(table, TUESDAY, minutes("7:05 AM"), "next", "Ramsey Rt 17 Train",
                      by=minutes("7:36 AM"))
    assert [t["reached_at"] for t in late["trips"]] == ["7:35 AM"]
    # Without a stop there is nothing to arrive at.
    nostop = departures(table, TUESDAY, minutes("4:00 AM"), "next", None, by=minutes("8:00 AM"))
    assert nostop["by_needs_a_stop"] is True and "by" not in nostop


def test_a_ride_back_to_campus_is_timed_by_the_pickup_at_the_stop() -> None:
    from datetime import date as day_of

    from rockygpt_brain.shuttle import departures
    # The loop calls at the train going out (9:50) and again coming back (10:35).
    table = [{"route": "Loop", "service_day": "weekday", "trips": [
        {"sequence": 1, "departure": "8:40 PM", "arrival": "9:45 PM", "stops": [
            {"location": "Train", "time": "8:50 PM"}, {"location": "Mall", "time": "9:15 PM"},
            {"location": "Train", "time": "9:35 PM"}]},
        {"sequence": 2, "departure": "9:40 PM", "arrival": "10:45 PM", "stops": [
            {"location": "Train", "time": "9:50 PM"}, {"location": "Mall", "time": "10:15 PM"},
            {"location": "Train", "time": "10:35 PM"}]}]}]
    thursday = day_of(2026, 10, 15)
    last = departures(table, thursday, 15 * 60, "last", "Train", back=True)
    assert last["back_to_campus"] is True
    assert [(t["leaves_campus"], t["leaves_stop_at"]) for t in last["trips"]] == [
        ("9:40 PM", "10:35 PM")]
    # At 10:20 PM the 9:40 trip has left campus, but its pickup at the train is still ahead.
    late = departures(table, thursday, 22 * 60 + 20, "next", "Train", back=True)
    assert [t["leaves_stop_at"] for t in late["trips"]] == ["10:35 PM"]
    going = departures(table, thursday, 22 * 60 + 20, "next", "Train")
    assert going["none_left"] is True
    # Going out, the second call is still said: it is the same trip coming back.
    out = departures(table, thursday, 15 * 60, "last", "Train")
    assert out["trips"][0]["reached_at"] == "9:50 PM"
    assert out["trips"][0]["leaves_stop_at"] == "10:35 PM"


def test_a_trip_that_only_goes_out_is_no_ride_back() -> None:
    # The Mid-Day page's last trip: out to the train at 5:30 PM, its return "N/A".
    table = [{"route": "Mid-Day", "service_day": "weekday", "trips": [
        {"sequence": 1, "departure": "4:45 PM", "arrival": "5:27 PM", "stops": [
            {"location": "Train", "time": "4:55 PM"}, {"location": "Train", "time": "5:17 PM"}]},
        {"sequence": 2, "departure": "5:30 PM", "arrival": "N/A", "stops": [
            {"location": "Train", "time": "5:40 PM"}]}]}]
    friday = date(2026, 10, 16)
    back = departures(table, friday, None, "last", "Train", back=True)
    assert [(t["leaves_stop_at"], t["back_on_campus"]) for t in back["trips"]] == [
        ("5:17 PM", "5:27 PM")]
    # After the last ride back has gone, the next day's first ride back is named, not the 5:30.
    late = departures(table, friday, 17 * 60 + 20, "next", "Train", back=True)
    assert late["none_left"] is True and late["next_service"]["trips"][0]["leaves_campus"] == (
        "4:45 PM")
    # Going out, the 5:30 PM trip is still the last one to the train.
    out = departures(table, friday, None, "last", "Train")
    assert out["trips"][0]["leaves_campus"] == "5:30 PM"
