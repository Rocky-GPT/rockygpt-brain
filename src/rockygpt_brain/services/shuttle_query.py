from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

CAMPUS_TIME_ZONE = "America/New_York"
CAMPUS_STOP = "Ramapo College"
MAX_LIMIT = 100
DEFAULT_LIMIT = 50

ShuttleServiceDay = Literal["weekday", "saturday", "sunday"]


@dataclass
class ShuttleTripRecord:
    route: str
    departure: str
    arrival: str
    stops: list[dict[str, str]]
    source_id: str


@dataclass
class TimedStop:
    stop: dict[str, str]
    minutes: int | None


@dataclass
class DatedTrip:
    trip: ShuttleTripRecord
    service_date: str
    service_day: ShuttleServiceDay


@dataclass
class MatchedTrip:
    trip: ShuttleTripRecord
    service_date: str
    service_day: ShuttleServiceDay
    departure: TimedStop
    stops: list[TimedStop]
    arrival: TimedStop
    origin: TimedStop
    destination: TimedStop
    origin_minutes: int | None
    destination_minutes: int | None
    origin_sort_minutes: int | None


def normalize(value: str) -> str:
    val = value.lower().replace("&", " and ")
    val = re.sub(r"\broute\b", "route", val)
    val = re.sub(r"[^a-z0-9\s]", " ", val)
    return re.sub(r"\s+", " ", val).strip()


def route_aliases(route: str) -> set[str]:
    aliases = {normalize(route)}
    if re.search(r"roadrunner", route, re.IGNORECASE):
        aliases.add("roadrunner")
        aliases.add("roadrunner express")
        aliases.add("ramapo roadrunner express")
    if re.search(r"ramsey.*17", route, re.IGNORECASE):
        for alias in (
            "ramsey",
            "ramsey express",
            "ramsey route 17",
            "ramsey route 17 express",
            "route 17 express",
            "train loop",
            "express train loop",
        ):
            aliases.add(alias)
    return aliases


def route_matches(route: str, requested: str | None) -> bool:
    if not requested:
        return True
    return normalize(requested) in route_aliases(route)


def stop_aliases(location: str) -> set[str]:
    norm = normalize(location)
    without_role = normalize(
        re.sub(r"\((?:drop[- ]?off|pick[- ]?up)\)", "", location, flags=re.IGNORECASE)
    )
    aliases = {norm, without_role}
    if norm == normalize(CAMPUS_STOP):
        for alias in (
            "campus",
            "ramapo",
            "ramapo campus",
            "ramapo college campus",
            "bradley center",
            "health services",
        ):
            aliases.add(alias)
    if "ramsey rt 17" in norm:
        for alias in (
            "ramsey",
            "ramsey station",
            "ramsey train station",
            "ramsey rt 17",
            "ramsey rt 17 station",
            "ramsey route 17",
            "ramsey route 17 station",
            "route 17 station",
        ):
            aliases.add(alias)
    if norm == "garden state plaza":
        aliases.add("gsp")
    if "barnes and noble" in norm:
        aliases.add("barnes and noble")
        aliases.add("barnes noble")
        aliases.add("fashion center")
    if "citymd" in norm:
        aliases.add("city md ramsey")
    return aliases


def stop_matches(location: str, requested: str) -> bool:
    return normalize(requested) in stop_aliases(location)


def clock_minutes(value: str) -> int | None:
    trimmed = value.strip()
    twelve = re.match(r"^(\d{1,2})(?::\s?(\d{2}))?\s*([ap])\.?\s*m\.?$", trimmed, re.IGNORECASE)
    if twelve:
        raw_hour = int(twelve.group(1))
        minute = int(twelve.group(2) or 0)
        if raw_hour < 1 or raw_hour > 12 or minute > 59:
            return None
        hour = (raw_hour % 12) + (12 if twelve.group(3).lower() == "p" else 0)
        return hour * 60 + minute
    twenty_four = re.match(r"^(\d{1,2}):(\d{2})$", trimmed)
    if not twenty_four:
        return None
    hour = int(twenty_four.group(1))
    minute = int(twenty_four.group(2))
    return hour * 60 + minute if (hour <= 23 and minute <= 59) else None


def monotonic_stops(stops: list[dict[str, str]]) -> list[TimedStop]:
    previous: int | None = None
    timed: list[TimedStop] = []
    for stop in stops:
        minutes = clock_minutes(stop.get("time", ""))
        if minutes is not None and previous is not None:
            while minutes < previous:
                minutes += 24 * 60
        if minutes is not None:
            previous = minutes
        timed.append(TimedStop(stop=stop, minutes=minutes))
    return timed


def campus_date(dt: datetime) -> str:
    tz = ZoneInfo(CAMPUS_TIME_ZONE)
    local = dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
    return local.strftime("%Y-%m-%d")


def campus_minutes(dt: datetime) -> int:
    tz = ZoneInfo(CAMPUS_TIME_ZONE)
    local = dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
    return local.hour * 60 + local.minute


def date_ordinal(d_str: str) -> int:
    y, m, d = map(int, d_str.split("-"))
    return date(y, m, d).toordinal()


def reference_minutes(as_of: datetime, service_date: str) -> int:
    as_of_date = campus_date(as_of)
    return (date_ordinal(as_of_date) - date_ordinal(service_date)) * 24 * 60 + campus_minutes(as_of)


def previous_date(d_str: str) -> str:
    y, m, d = map(int, d_str.split("-"))
    return (date(y, m, d) - timedelta(days=1)).isoformat()


def service_day_for_date(d_str: str) -> ShuttleServiceDay:
    y, m, d = map(int, d_str.split("-"))
    weekday = date(y, m, d).weekday()  # Monday=0, Saturday=5, Sunday=6
    if weekday == 5:
        return "saturday"
    if weekday == 6:
        return "sunday"
    return "weekday"


def match_trip(
    dated_trip: DatedTrip,
    origin_query: str | None,
    destination_query: str | None,
) -> MatchedTrip | None:
    trip = dated_trip.trip
    service_date = dated_trip.service_date
    service_day = dated_trip.service_day
    arrival_location = "End of service" if clock_minutes(trip.arrival) is None else CAMPUS_STOP
    raw_stops = (
        [{"location": CAMPUS_STOP, "time": trip.departure}]
        + trip.stops
        + [{"location": arrival_location, "time": trip.arrival}]
    )
    timed = monotonic_stops(raw_stops)
    departure = timed[0]
    arrival = timed[-1]

    origin_index = 0
    if origin_query:
        candidates = [
            (idx, entry)
            for idx, entry in enumerate(timed)
            if stop_matches(entry.stop.get("location", ""), origin_query)
        ]
        if not candidates:
            return None
        origin_index = (
            candidates[0][0] if stop_matches(CAMPUS_STOP, origin_query) else candidates[-1][0]
        )

    destination_index = len(timed) - 1
    if destination_query:
        candidates = [
            (idx, entry)
            for idx, entry in enumerate(timed)
            if idx > origin_index
            and stop_matches(entry.stop.get("location", ""), destination_query)
        ]
        if not candidates:
            return None
        destination_index = (
            candidates[-1][0] if stop_matches(CAMPUS_STOP, destination_query) else candidates[0][0]
        )

    if destination_index <= origin_index:
        return None

    origin = timed[origin_index]
    destination = timed[destination_index]
    last_known = next((t.minutes for t in reversed(timed) if t.minutes is not None), None)

    return MatchedTrip(
        trip=trip,
        service_date=service_date,
        service_day=service_day,
        departure=departure,
        stops=timed[1:-1],
        arrival=arrival,
        origin=origin,
        destination=destination,
        origin_minutes=origin.minutes,
        destination_minutes=destination.minutes if destination.minutes is not None else last_known,
        origin_sort_minutes=(
            None
            if origin.minutes is None
            else date_ordinal(service_date) * 24 * 60 + origin.minutes
        ),
    )


def execute_shuttle_query(
    trips: list[DatedTrip],
    query: dict[str, Any],
    as_of: datetime,
) -> list[dict[str, Any]]:
    route_filter = query.get("route")
    origin_filter = query.get("origin")
    destination_filter = query.get("destination")
    time_scope = query.get("timeScope", "remaining")
    selection = query.get("selection", "all")
    limit = query.get("limit", DEFAULT_LIMIT)

    entity_matches: list[MatchedTrip] = []
    for dated_trip in trips:
        if route_matches(dated_trip.trip.route, route_filter):
            match = match_trip(dated_trip, origin_filter, destination_filter)
            if match:
                entity_matches.append(match)

    time_matches: list[MatchedTrip] = []
    for m in entity_matches:
        if time_scope == "full_day":
            time_matches.append(m)
        elif m.origin_minutes is not None:
            ref = reference_minutes(as_of, m.service_date)
            if time_scope == "remaining" and m.origin_minutes > ref:
                time_matches.append(m)
            elif (
                time_scope == "at_time"
                and m.destination_minutes is not None
                and m.origin_minutes <= ref < m.destination_minutes
            ):
                time_matches.append(m)

    def sort_key(item: MatchedTrip) -> tuple[float, str, str]:
        by_time = float("inf") if item.origin_sort_minutes is None else item.origin_sort_minutes
        return (by_time, item.trip.route, item.trip.departure)

    selected = sorted(time_matches, key=sort_key)
    if selection in ("first", "next"):
        selected = selected[:1]

    bounded = selected[:limit]
    records: list[dict[str, Any]] = []
    for m in bounded:
        records.append(
            {
                "route": m.trip.route,
                "serviceDate": m.service_date,
                "serviceDay": m.service_day,
                "departure": m.departure.stop,
                "stops": [s.stop for s in m.stops],
                "arrival": m.arrival.stop,
                "matchedOrigin": m.origin.stop,
                "matchedDestination": m.destination.stop,
                "evidenceIds": [f"source:{m.trip.source_id}"],
            }
        )
    return records
