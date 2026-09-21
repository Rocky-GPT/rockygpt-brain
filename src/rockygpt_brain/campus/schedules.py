"""Deterministic timetable arithmetic over complete, dated search results.

A published timetable proves scheduled times only. Ambiguous wall clocks,
unknown coverage, conflicting trips and partial searches never prove 'next'.
"""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.retrieval.models import SearchQuery

CAMPUS_ZONE = ZoneInfo("America/New_York")


def wall_time(value: str, day: date) -> datetime:
    """Read explicit 12/24-hour clocks; reject DST gaps and ambiguous folds."""
    match = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?\s*", value, re.IGNORECASE)
    if not match:
        raise ValueError("Unrecognized schedule time")
    hour, minute = int(match[1]), int(match[2] or 0)
    if match[3]:
        if not 1 <= hour <= 12:
            raise ValueError("Invalid 12-hour time")
        hour = hour % 12 + (12 if match[3].upper() == "PM" else 0)
    elif match[2] is None:
        raise ValueError("An unqualified hour is ambiguous")
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=CAMPUS_ZONE)
    if (
        local.astimezone(UTC).astimezone(CAMPUS_ZONE) != local
        or local.replace(fold=1).utcoffset() != local.utcoffset()
    ):
        raise ValueError("Ambiguous or nonexistent campus-local schedule time")
    return local


def trip_times(fields: dict[str, Any], day_offset: int = 0) -> list[tuple[str, datetime]]:
    """Preserve endpoint meaning and stop order, including explicit overnight trips."""
    day = date.fromisoformat(fields["service_date"]) + timedelta(days=day_offset)
    stops = fields["stops"]
    if not isinstance(stops, list):
        raise ValueError("Missing stop sequence")
    points = [("campus", fields["campus_departure"])]
    points.extend((stop["location"], stop["time"]) for stop in stops)
    points.append(("campus", fields["campus_return"]))
    result: list[tuple[str, datetime]] = []
    for location, value in points:
        if not isinstance(location, str) or not location.strip() or not isinstance(value, str):
            raise ValueError("Unknown stop or time")
        instant = wall_time(value, day)
        if result and instant < result[-1][1]:
            day += timedelta(days=1)
            instant = wall_time(value, day)
        result.append((location, instant))
    # A whole weekly shuttle trip cannot be silently interpreted as multiple days.
    # Longer or contradictory records remain visible but need clarification.
    if result[-1][1].timestamp() - result[0][1].timestamp() > 12 * 3600:
        raise ValueError("Unverified overnight stop sequence")
    return result


def opening_intervals(
    schedule: str | list[dict[str, Any]], day: date
) -> list[tuple[datetime, datetime]]:
    """Validated intervals for one service day; closure is empty, unknown raises."""
    result: list[tuple[datetime, datetime]] = []
    if isinstance(schedule, list):
        for interval in schedule:
            if not isinstance(interval, dict):
                raise ValueError("Malformed opening interval")
            clocks = [interval.get("open"), interval.get("close")]
            if any(not isinstance(clock, str) or not re.fullmatch(r"\d{2}:\d{2}", clock)
                   for clock in clocks):
                raise ValueError("Opening intervals require explicit HH:MM clocks")
            offset = interval.get("close_day_offset", 0)
            if type(offset) is not int or offset not in (0, 1):
                raise ValueError("Invalid closing day offset")
            start = wall_time(str(clocks[0]), day)
            end = wall_time(str(clocks[1]), day + timedelta(days=offset))
            # A next-day marker cannot turn contradictory/equal clocks into a full day.
            wall_duration = end.replace(tzinfo=None) - start.replace(tzinfo=None)
            if not timedelta(0) < wall_duration < timedelta(days=1):
                raise ValueError("Invalid opening interval duration")
            result.append((start, end))
    elif isinstance(schedule, str):
        if schedule.strip().casefold() in {"closed", "closed (seasonal closure)"}:
            return []
        for text_interval in re.split(r"\s+and\s+|;|,", schedule, flags=re.IGNORECASE):
            # Labels identify meal periods; they do not alter the supplied clocks.
            text_interval = re.sub(r"^[^:\d]+:\s*(?=\d{1,2}:\d{2})", "", text_interval.strip())
            text_clocks = re.split(
                r"\s*(?:[-–—]|\bto\b)\s*", text_interval, flags=re.IGNORECASE
            )
            if len(text_clocks) != 2:
                raise ValueError("Schedule does not establish explicit opening intervals")
            start, end = (wall_time(value, day) for value in text_clocks)
            if end < start:
                end = wall_time(text_clocks[1], day + timedelta(days=1))
            if start == end:
                raise ValueError("Equal clocks do not establish 24-hour service")
            result.append((start, end))
    else:
        raise ValueError("Unknown opening hours")
    ordered = sorted(result)
    if any(
        next_start < end for (_, end), (next_start, _) in zip(ordered, ordered[1:], strict=False)
    ):
        raise ValueError("Overlapping opening intervals")
    return result


def departure_summary(output: dict[str, Any], query: SearchQuery, now: datetime) -> dict[str, Any]:
    """Next/last scheduled departure per route and origin, within retrieved dates.

    Only an unranked, complete search can establish an extremum. The source rows
    stay intact; results reference their IDs and cannot expand their coverage.
    """
    unavailable = {"status": "unavailable", "reason": "incomplete_schedule_coverage"}
    records = output.get("records", [])
    if (
        query.collection != "shuttle"
        or query.query.strip()
        or output.get("status") != "ok"
        or output.get("truncated") is not False
        or output.get("total_matches") != len(records)
        or not records
        or query.date_from is None
        or now.tzinfo is None
    ):
        return unavailable
    assert query.date_from is not None
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    identities: dict[tuple[str, str, int], str] = {}
    service_clocks: dict[tuple[str, str], tuple[datetime, int]] = {}
    try:
        ordered = sorted(
            records,
            key=lambda r: (
                r["fields"]["route"],
                r["fields"]["service_date"],
                r["fields"]["sequence"],
            ),
        )
        for record in ordered:
            fields = record["fields"]
            day = date.fromisoformat(fields["service_date"])
            if (
                record.get("collection") != "shuttle"
                or record.get("freshness") not in {"fresh", "static"}
                or record.get("trust_tier") not in {"official_primary", "official_secondary"}
                or not record.get("entity_id")
                or not record.get("source_key")
                or record.get("content_truncated")
                or type(fields.get("sequence")) is not int
                or fields["sequence"] < 0
                or not query.date_from <= day <= (query.date_to or query.date_from)
                or any(
                    record.get("coverage", {}).get("fields", {}).get(field) != "published"
                    for field in (
                        "campus_departure",
                        "campus_return",
                        "stops",
                        "route",
                        "service_day",
                    )
                )
                or fields["service_day"]
                != ("weekday" if day.weekday() < 5 else day.strftime("%A").lower())
                or (record.get("valid_from") and day < date.fromisoformat(record["valid_from"]))
                or (record.get("valid_until") and day > date.fromisoformat(record["valid_until"]))
            ):
                return unavailable
            identity = (fields["route"], str(day), fields["sequence"])
            # Multiple source versions of one scheduled trip need resolution.
            if identity in identities or record["id"] in identities.values():
                return {"status": "unavailable", "reason": "conflicting_schedule_records"}
            identities[identity] = record["id"]
            service = (fields["route"], str(day))
            previous = service_clocks.get(service)
            day_offset = previous[1] if previous else 0
            points = trip_times(fields, day_offset)
            if previous and points[0][1] < previous[0]:
                if day_offset or previous[0].hour < 18 or points[0][1].hour > 6:
                    raise ValueError("Unverified service-day rollover")
                day_offset = 1
                points = trip_times(fields, day_offset)
            service_clocks[service] = (points[0][1], day_offset)
            if len({name.casefold() for name, _ in points[:-1]}) != len(points) - 1:
                return {"status": "unavailable", "reason": "ambiguous_stop_identity"}
            for index, (origin, departure) in enumerate(points[:-1]):
                groups.setdefault((fields["route"], origin), []).append(
                    {
                        "evidence_id": record["id"],
                        "departure_at": departure.isoformat(),
                        "remaining_stops": [
                            {
                                "location": name,
                                "scheduled_at": instant.isoformat(),
                            }
                            for name, instant in points[index + 1 :]
                        ],
                        "elapsed_minutes_to_return": (
                            points[-1][1].timestamp() - departure.timestamp()
                        )
                        / 60,
                        "origin_restriction": (
                            fields["stops"][index - 1].get("restriction") if index else None
                        ),
                        "origin_label": origin,
                    }
                )
    except (KeyError, TypeError, ValueError):
        return {"status": "unavailable", "reason": "unverified_schedule_time"}
    result = []
    for (route, origin), trips in sorted(groups.items()):
        trips.sort(key=lambda trip: datetime.fromisoformat(trip["departure_at"]).timestamp())
        remaining = [
            trip
            for trip in trips
            if datetime.fromisoformat(trip["departure_at"]).timestamp() > now.timestamp()
        ]
        result.append(
            {
                "route": route,
                "origin": origin,
                "next": remaining[0] if remaining else None,
                "last": remaining[-1] if remaining else None,
                "scheduled_departure_count": len(trips),
                "remaining_departure_count": len(remaining),
            }
        )
    return {
        "status": "ok",
        "as_of": now.isoformat(),
        "date_from": str(query.date_from),
        "date_to": str(query.date_to or query.date_from),
        "departures": result,
        "limitations": [
            "Scheduled times only; live delays and holiday operations are unknown.",
            "Next and last are within the retrieved dates only; null does not mean service ends.",
            "Preserve source pickup/drop-off restrictions. No walking or eating time is assumed.",
        ],
    }


def schedule_references(summary: dict[str, Any]) -> dict[tuple[str, str, str], set[str]]:
    """Keep validated calculated values available to the arithmetic tool by source ID.

    Arrival back at campus and departure from campus are deliberately different
    fields. Only a successful complete timetable calculation can populate these.
    """
    references: dict[tuple[str, str, str], set[str]] = {}
    if summary.get("status") != "ok":
        return references
    for group in summary["departures"]:
        for selection in ("next", "last"):
            trip = group[selection]
            if trip is None:
                continue
            reference = (trip["evidence_id"], "scheduled_departure", group["origin"])
            references.setdefault(reference, set()).add(trip["departure_at"])
            for stop in trip["remaining_stops"]:
                reference = (trip["evidence_id"], "scheduled_arrival", stop["location"])
                references.setdefault(reference, set()).add(stop["scheduled_at"])
    return references
