"""Timetable extrema, endpoint meaning, coverage, overnight and DST boundaries."""

from copy import deepcopy
from datetime import date, datetime
from typing import Any

import pytest

from rockygpt_brain.campus.schedules import (
    CAMPUS_ZONE,
    departure_summary,
    opening_intervals,
    trip_times,
    wall_time,
)
from rockygpt_brain.retrieval.data import SearchQuery

NOW = datetime(2026, 9, 16, 16, tzinfo=CAMPUS_ZONE)
QUERY = SearchQuery(collection="shuttle", date_from=NOW.date(), limit=50)


def record(sequence: int, departure: str, stop: str, returned: str) -> dict[str, Any]:
    fields = {
        "sequence": sequence,
        "route": "Station route",
        "service_day": "weekday",
        "service_date": str(NOW.date()),
        "campus_departure": departure,
        "campus_return": returned,
        "stops": [{"location": "Station", "time": stop, "restriction": "Pickup only"}],
    }
    return {
        "id": f"shuttle:{sequence}",
        "entity_id": f"trip:{sequence}",
        "source_key": "shuttle",
        "collection": "shuttle",
        "fields": fields,
        "freshness": "fresh",
        "trust_tier": "official_primary",
        "coverage": {"fields": {key: "published" for key in fields}},
    }


def output(*records: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "records": list(records),
        "total_matches": len(records),
        "truncated": False,
    }


def test_next_and_last_are_computed_from_all_trips_and_correct_origin() -> None:
    rows = output(
        record(2, "5:30 PM", "5:40 PM", "6:00 PM"), record(1, "3:50 PM", "4:10 PM", "4:30 PM")
    )
    before = deepcopy(rows)
    summary = departure_summary(rows, QUERY, NOW)
    assert summary["status"] == "ok"
    station, campus = summary["departures"]
    assert campus["origin"] == "campus"
    assert campus["next"]["departure_at"] == "2026-09-16T17:30:00-04:00"
    assert station["next"]["departure_at"] == "2026-09-16T16:10:00-04:00"
    assert station["next"]["remaining_stops"] == [
        {
            "location": "campus",
            "scheduled_at": "2026-09-16T16:30:00-04:00",
        }
    ]
    assert station["last"]["evidence_id"] == "shuttle:2"
    assert rows == before  # Original restrictions and evidence remain intact.


@pytest.mark.parametrize(
    "change",
    [
        {"truncated": True},
        {"total_matches": 2},
        {"status": "no_match"},
    ],
)
def test_partial_search_never_establishes_next(change: dict[str, Any]) -> None:
    rows = output(record(1, "5:30 PM", "5:40 PM", "6:00 PM"))
    assert departure_summary({**rows, **change}, QUERY, NOW)["status"] == "unavailable"
    assert (
        departure_summary(rows, QUERY.model_copy(update={"query": "5:30"}), NOW)["status"]
        == "unavailable"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"freshness": "stale"},
        {"trust_tier": "unknown"},
        {"coverage": {}},
        {"valid_until": "2026-09-15"},
        {"valid_from": "2026-09-17"},
        {"entity_id": None},
        {"content_truncated": True},
    ],
)
def test_unverified_records_do_not_support_arithmetic(change: dict[str, Any]) -> None:
    trip = {**record(1, "5:30 PM", "5:40 PM", "6:00 PM"), **change}
    assert departure_summary(output(trip), QUERY, NOW)["status"] == "unavailable"


def test_conflicting_trips_are_not_silently_chosen() -> None:
    first = record(1, "5:30 PM", "5:40 PM", "6:00 PM")
    second = {**record(1, "5:35 PM", "5:45 PM", "6:05 PM"), "id": "other:1"}
    assert (
        departure_summary(output(first, second), QUERY, NOW)["reason"]
        == "conflicting_schedule_records"
    )


def test_no_later_trip_does_not_claim_service_is_closed() -> None:
    summary = departure_summary(output(record(1, "3 PM", "3:10 PM", "3:30 PM")), QUERY, NOW)
    assert all(item["next"] is None for item in summary["departures"])
    assert any("retrieved dates only" in value for value in summary["limitations"])


def test_overnight_endpoints_and_later_service_rows_use_next_date() -> None:
    evening = record(1, "11:50 PM", "12:10 AM", "12:30 AM")
    early = record(2, "12:45 AM", "1:00 AM", "1:15 AM")
    points = trip_times(evening["fields"])
    assert str(points[1][1].date()) == "2026-09-17"
    assert (points[-1][1].timestamp() - points[0][1].timestamp()) / 60 == 40
    result = departure_summary(output(early, evening), QUERY, NOW)
    campus = next(item for item in result["departures"] if item["origin"] == "campus")
    assert campus["last"]["departure_at"] == "2026-09-17T00:45:00-04:00"


def test_non_overnight_backwards_sequence_is_not_guessed() -> None:
    result = departure_summary(
        output(
            record(1, "3 PM", "3:10 PM", "3:30 PM"),
            record(2, "2 PM", "2:10 PM", "2:30 PM"),
        ),
        QUERY,
        NOW,
    )
    assert result["status"] == "unavailable"


@pytest.mark.parametrize(
    ("day", "clock"),
    [
        (date(2026, 3, 8), "2:30 AM"),
        (date(2026, 11, 1), "1:30 AM"),
        (date(2026, 9, 16), "5"),
        (date(2026, 9, 16), "5:90 PM"),
        (date(2026, 9, 16), "5 PM (pickup only)"),
    ],
)
def test_ambiguous_invalid_or_nonexistent_times_fail_closed(day: date, clock: str) -> None:
    with pytest.raises(ValueError):
        wall_time(clock, day)


def test_elapsed_duration_uses_actual_dst_elapsed_time() -> None:
    trip = record(1, "1:30 AM", "3:00 AM", "3:30 AM")
    trip["fields"]["service_date"] = "2026-03-08"
    points = trip_times(trip["fields"])
    assert points[-1][1].timestamp() - points[0][1].timestamp() == 3600


@pytest.mark.parametrize("schedule", [
    "8:00am-9:30am and 11:30am-12:30pm",
    [{"open": "08:00", "close": "09:30"}, {"open": "11:30", "close": "12:30"}],
])
def test_split_opening_intervals_preserve_the_closed_gap(schedule: Any) -> None:
    day = date(2026, 9, 21)
    intervals = opening_intervals(schedule, day)
    assert [(start.strftime("%H:%M"), end.strftime("%H:%M")) for start, end in intervals] == [
        ("08:00", "09:30"), ("11:30", "12:30"),
    ]
    gap = datetime(2026, 9, 21, 10, tzinfo=CAMPUS_ZONE)
    assert not any(start <= gap < end for start, end in intervals)


def test_structured_midnight_and_closure_keep_service_day_meaning() -> None:
    day = date(2026, 9, 21)
    start, end = opening_intervals(
        [{"open": "08:00", "close": "00:00", "close_day_offset": 1}], day
    )[0]
    assert start.date() == day and end.date() == date(2026, 9, 22)
    assert opening_intervals([], day) == opening_intervals("CLOSED", day) == []


@pytest.mark.parametrize("schedule", [
    None, "", "CLOSED until noon", "9am-5pm and unknown",
    [{"open": "08:00", "close": "00:00"}],
    [{"open": "08:00", "close": "08:00", "close_day_offset": 1}],
    [{"open": "08:00", "close": "17:00", "close_day_offset": True}],
    [{"open": "25:00", "close": "26:00"}],
    [{"open": "08:00", "close": "12:00"}, {"open": "11:00", "close": "13:00"}],
])
def test_unknown_or_invalid_opening_hours_never_mean_closed(schedule: Any) -> None:
    with pytest.raises(ValueError):
        opening_intervals(schedule, date(2026, 9, 21))
