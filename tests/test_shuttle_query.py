from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from rockygpt_brain.services.shuttle_query import (
    CAMPUS_TIME_ZONE,
    DatedTrip,
    ShuttleTripRecord,
    clock_minutes,
    execute_shuttle_query,
    monotonic_stops,
    normalize,
    route_aliases,
    route_matches,
    stop_matches,
)

NOW = datetime(2026, 9, 16, 14, 0, tzinfo=ZoneInfo(CAMPUS_TIME_ZONE))


def test_normalize_strips_punctuation_and_standardizes_words() -> None:
    assert normalize("Main Loop & Express!!") == "main loop and express"
    assert normalize("  Route   17   ") == "route 17"


def test_route_aliases_and_matching() -> None:
    assert route_matches("Main Loop", "main loop") is True
    assert route_matches("Main Loop", "other") is False
    assert "roadrunner express" in route_aliases("Roadrunner")
    assert "train loop" in route_aliases("Ramsey Route 17 Express")


def test_stop_aliases_and_matching() -> None:
    assert stop_matches("Ramapo College", "campus") is True
    assert stop_matches("Ramapo College", "bradley center") is True
    assert stop_matches("Ramsey Rt 17 Station", "ramsey train station") is True
    assert stop_matches("Garden State Plaza", "gsp") is True


def test_clock_minutes_parsing() -> None:
    assert clock_minutes("7:25 AM") == 7 * 60 + 25
    assert clock_minutes("1:05 PM") == 13 * 60 + 5
    assert clock_minutes("12:00 AM") == 0
    assert clock_minutes("12:00 PM") == 12 * 60
    assert clock_minutes("14:30") == 14 * 60 + 30
    assert clock_minutes("invalid") is None


def test_monotonic_stops_crossing_midnight() -> None:
    stops = [
        {"location": "Ramapo College", "time": "11:30 PM"},
        {"location": "Ramsey", "time": "12:15 AM"},
        {"location": "Ramapo College", "time": "12:45 AM"},
    ]
    timed = monotonic_stops(stops)
    assert timed[0].minutes == 23 * 60 + 30
    assert timed[1].minutes == 24 * 60 + 15
    assert timed[2].minutes == 24 * 60 + 45


def test_execute_shuttle_query_filtering() -> None:
    trips = [
        DatedTrip(
            trip=ShuttleTripRecord(
                route="Main Loop",
                departure="1:00 PM",
                arrival="1:45 PM",
                stops=[{"location": "Ramsey Rt 17", "time": "1:20 PM"}],
                source_id="src_1",
            ),
            service_date="2026-09-16",
            service_day="weekday",
        ),
        DatedTrip(
            trip=ShuttleTripRecord(
                route="Main Loop",
                departure="3:00 PM",
                arrival="3:45 PM",
                stops=[{"location": "Ramsey Rt 17", "time": "3:20 PM"}],
                source_id="src_1",
            ),
            service_date="2026-09-16",
            service_day="weekday",
        ),
    ]

    # Remaining scope (now = 2:00 PM) -> only 3:00 PM trip
    remaining = execute_shuttle_query(
        trips,
        {"timeScope": "remaining", "asOf": NOW.isoformat()},
        NOW,
    )
    assert len(remaining) == 1
    assert remaining[0]["departure"] == {"location": "Ramapo College", "time": "3:00 PM"}

    # Full day scope -> both trips
    full_day = execute_shuttle_query(
        trips,
        {"timeScope": "full_day", "asOf": NOW.isoformat()},
        NOW,
    )
    assert len(full_day) == 2
