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
# The published "Arrive on Campus" column says N/A for a trip that ends at its
# last stop instead of returning. Any other non-clock value stays an error.
NO_CAMPUS_RETURN = re.compile(r"\s*N/?A\s*", re.IGNORECASE)


# Some routes publish one station as an "Arrive X" row and a later "Depart X"
# row. Students board at the Depart row; the bus reaches the Arrive row.
ARRIVAL_ROW = re.compile(r"\s*arrive\b", re.IGNORECASE)
DEPARTURE_ROW = re.compile(r"\s*depart\b", re.IGNORECASE)
# What a departure question can ask of a day's timetable.
SELECTIONS = ("first", "next", "last")


def returns_to_campus(fields: dict[str, Any]) -> bool:
    value = fields.get("campus_return")
    return not (isinstance(value, str) and NO_CAMPUS_RETURN.fullmatch(value))


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


def meal_order(periods: list[dict[str, Any]], day: date, now: datetime) -> list[str]:
    """One day's published meal labels in the order a student asking now needs them.

    On the campus date of `now`: the meal in service, then later meals by start, then
    meals already served, most recent first, so a late question about tonight finds
    dinner before breakfast. Another day keeps start order. A period whose clocks cannot
    be read establishes no position and is left out.
    """
    today = now.astimezone(CAMPUS_ZONE).date() == day
    keys: dict[str, tuple[int, float]] = {}
    for period in periods:
        label = period.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        try:
            start = wall_time(str(period.get("start", "")), day)
            end = wall_time(str(period.get("end", "")), day)
            if end <= start:  # Service past midnight ends the next day.
                end = wall_time(str(period.get("end", "")), day + timedelta(days=1))
        except ValueError:
            continue
        state = 0 if not today or start <= now < end else 1 if now < start else 2
        position = (state, -start.timestamp() if state == 2 else start.timestamp())
        keys[label] = min(keys.get(label, position), position)
    return sorted(keys, key=keys.__getitem__)


def trip_times(fields: dict[str, Any], day_offset: int = 0) -> list[tuple[str, datetime]]:
    """Preserve endpoint meaning and stop order, including explicit overnight trips."""
    day = date.fromisoformat(fields["service_date"]) + timedelta(days=day_offset)
    stops = fields["stops"]
    if not isinstance(stops, list):
        raise ValueError("Missing stop sequence")
    points = [("campus", fields["campus_departure"])]
    points.extend((stop["location"], stop["time"]) for stop in stops)
    if returns_to_campus(fields):
        points.append(("campus", fields["campus_return"]))
    elif not stops:
        raise ValueError("A trip needs a stop or a campus return")
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
    """First/next/last scheduled departure per route and origin, and per stop reached,
    within retrieved dates.

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
    # A stop visited twice in one trip (a loop back to the train) has no single
    # departure time, so that route's next/last from that stop is withheld.
    ambiguous: dict[tuple[str, str], str] = {}
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
            names = [name.casefold() for name, _ in points[:-1]]
            for name, _ in points[:-1]:
                if names.count(name.casefold()) > 1:
                    ambiguous.setdefault((fields["route"], name.casefold()), name)
            returns = returns_to_campus(fields)
            for index, (origin, departure) in enumerate(points[:-1]):
                if (fields["route"], origin.casefold()) in ambiguous or (
                    index and ARRIVAL_ROW.match(origin)
                ):
                    continue
                groups.setdefault((fields["route"], origin), []).append(
                    {
                        "evidence_id": record["id"],
                        "service_date": str(day),
                        "departure_at": departure.isoformat(),
                        "remaining_stops": [
                            {
                                "location": name,
                                "scheduled_at": instant.isoformat(),
                            }
                            for name, instant in points[index + 1 :]
                        ],
                        "elapsed_minutes_to_return": (
                            (points[-1][1].timestamp() - departure.timestamp()) / 60
                            if returns
                            else None
                        ),
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
        if (route, origin.casefold()) in ambiguous:
            continue
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
                # "What's the first shuttle today?" was answered with the next one (09-28):
                # the day's first departure is its own selection, whatever the time now.
                "first": trips[0] if trips else None,
                "next": remaining[0] if remaining else None,
                "last": remaining[-1] if remaining else None,
                "scheduled_departure_count": len(trips),
                "remaining_departure_count": len(remaining),
                "days": _days(trips),
                "destinations": _destinations(trips, now),
            }
        )
    withheld = [
        {"route": route, "origin": origin, "reason": "ambiguous_stop_identity"}
        for (route, _), origin in sorted(ambiguous.items())
    ]
    return {
        "status": "ok",
        "as_of": now.isoformat(),
        "date_from": str(query.date_from),
        "date_to": str(query.date_to or query.date_from),
        "departures": result,
        "withheld_origins": withheld,
        "limitations": [
            "Scheduled times only; live delays and holiday operations are unknown.",
            "First is the earliest scheduled departure in the retrieved dates, whatever the "
            "time now. Next and last are within the retrieved dates only; null does not mean "
            "service ends. destinations gives, per stop, the first, next and last departure "
            "that reaches it.",
            "days gives each service date's first and last scheduled departure whatever the "
            "time now. A departure before as_of is one whose scheduled time has passed; the "
            "timetable can't say a bus actually left. next is the one after as_of.",
            "Preserve source pickup/drop-off restrictions. No walking or eating time is assumed.",
            *(
                ["A withheld origin repeats within a trip; its next/last departure is unknown."]
                if withheld
                else []
            ),
        ],
    }


def _days(trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each service date's first and last departure, whatever the time now. "When is the
    last shuttle?" asked at 11:10 PM (09-28) found no later departure and said so for
    every route: the day's last had left at 9:40 PM, and nothing said when."""
    days: dict[str, list[dict[str, Any]]] = {}
    for trip in trips:
        days.setdefault(trip["service_date"], []).append(trip)
    return [
        {"service_date": day,
         **{selection: {key: chosen[key] for key in DAY_KEYS if key in chosen}
            for selection, chosen in (("first", ordered[0]), ("last", ordered[-1]))}}
        for day, ordered in sorted(days.items())
    ]


# What a day's first or last departure keeps: enough to name, cite and time it.
DAY_KEYS = ("evidence_id", "departure_at", "arrives_at", "origin_restriction")


def _destinations(trips: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """For each stop these trips reach, the first, next and last departure that gets there.

    "The first shuttle today that goes to Garden State Plaza" needs every trip checked,
    not a model reading a filtered list. A "Depart X" row is when the bus leaves X, and a
    stop one trip reaches twice has no single arrival, so neither is a destination.
    """
    reaching: dict[str, list[dict[str, Any]]] = {}
    for trip in trips:
        names = [stop["location"].casefold() for stop in trip["remaining_stops"]]
        for stop in trip["remaining_stops"]:
            if (stop["location"] == "campus" or DEPARTURE_ROW.match(stop["location"])
                    or names.count(stop["location"].casefold()) > 1):
                continue  # The return to campus is where every trip ends, not a stop.
            reaching.setdefault(stop["location"], []).append({
                "evidence_id": trip["evidence_id"],
                "service_date": trip["service_date"],
                "departure_at": trip["departure_at"],
                "arrives_at": stop["scheduled_at"],
                "origin_restriction": trip["origin_restriction"],
            })
    result = []
    for stop, reached in sorted(reaching.items()):
        remaining = [trip for trip in reached
                     if datetime.fromisoformat(trip["departure_at"]).timestamp() > now.timestamp()]
        result.append({"stop": stop, "first": reached[0],
                       "next": remaining[0] if remaining else None,
                       "last": remaining[-1] if remaining else None,
                       "days": _days(reached)})
    return result


# What a delivery-bounded summary keeps of each selection: enough to name, cite and time it.
# The trip's own record lists its stops.
BOUNDED_KEYS = ("evidence_id", "service_date", "departure_at", "origin_restriction")


def bounded_summaries(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Smaller forms of an ok summary, largest first, for a lookup the prompt has no room
    for whole.

    Per-stop destinations and each selection's stop list were three quarters of one
    weekday's summary (30 of 40 KB, against 13 KB for its 30 trips), so the summary was
    dropped with the first trip cut, and with it the proof that 7 AM was next (09-29).
    The first form keeps every boarding stop's first, next and last and each day's first
    and last; the second keeps campus's alone and withholds the other boarding stops.
    """
    if summary.get("status") != "ok":
        return []

    def row(group: dict[str, Any]) -> dict[str, Any]:
        return {
            "route": group["route"],
            "origin": group["origin"],
            **{selection: None if group[selection] is None
               else {key: group[selection][key] for key in BOUNDED_KEYS}
               for selection in SELECTIONS},
            "scheduled_departure_count": group["scheduled_departure_count"],
            "remaining_departure_count": group["remaining_departure_count"],
            "days": group["days"],
        }

    compact = {
        **summary,
        "departures": [row(group) for group in summary["departures"]],
        "destinations_withheld": "retrieval_delivery_limit",
        "limitations": [
            *summary["limitations"],
            "Per-stop destinations were left out of delivery, so which trip first, next or "
            "last reaches a stop is unknown here; each trip's record lists its stops.",
        ],
    }
    others = [group for group in summary["departures"] if group["origin"] != "campus"]
    if not others:
        return [compact]
    campus = {
        **compact,
        "departures": [row(group) for group in summary["departures"]
                       if group["origin"] == "campus"],
        "withheld_origins": [
            *summary["withheld_origins"],
            *({"route": group["route"], "origin": group["origin"],
               "reason": "retrieval_delivery_limit"} for group in others),
        ],
        "limitations": [
            *compact["limitations"],
            "Boarding stops other than campus were left out of delivery; their first, next "
            "and last departures are unknown here.",
        ],
    }
    return [compact, campus]


def departed_trips(records: list[dict[str, Any]], now: datetime) -> list[str]:
    """The trips whose campus departure has passed, which a bounded delivery sheds first:
    keeping them cut Tuesday's upcoming trips from a Monday-night lookup (09-29). A trip
    whose time can't be read counts as still to come."""
    departed = []
    for record in records:
        try:
            _, leaves = trip_times(record["fields"])[0]
        except (KeyError, TypeError, ValueError):
            continue
        if leaves <= now:
            departed.append(record["id"])
    return departed


def review_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """The calculation a reviewer needs to check a stated next/last departure.

    Stop lists stay in the cited records; the reviewer gets the computed
    selections and their scope, so it does not redo timetable arithmetic.
    """
    if summary.get("status") != "ok":
        return {"status": summary.get("status"), "reason": summary.get("reason")}
    return {
        **{
            key: summary[key]
            for key in (
                "status", "as_of", "date_from", "date_to", "withheld_origins", "limitations"
            )
        },
        # A summary kept over a delivery-bounded lookup says what it left out.
        **{key: summary[key] for key in ("delivery", "destinations_withheld") if key in summary},
        "departures": [
            {
                "route": group["route"],
                "origin": group["origin"],
                **{
                    selection: None
                    if group[selection] is None
                    else {
                        key: group[selection][key]
                        for key in ("evidence_id", "departure_at", "origin_restriction")
                    }
                    for selection in SELECTIONS
                },
                "scheduled_departure_count": group["scheduled_departure_count"],
                "remaining_departure_count": group["remaining_departure_count"],
                "days": group["days"],
                **({"destinations": group["destinations"]} if "destinations" in group else {}),
            }
            for group in summary["departures"]
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
        for selection in SELECTIONS:
            trip = group[selection]
            if trip is None:
                continue
            reference = (trip["evidence_id"], "scheduled_departure", group["origin"])
            references.setdefault(reference, set()).add(trip["departure_at"])
            names = [stop["location"].casefold() for stop in trip["remaining_stops"]]
            for stop in trip["remaining_stops"]:
                # A loop that reaches a stop twice has no single arrival there,
                # and a "Depart X" row is when the bus leaves, not arrives.
                if names.count(stop["location"].casefold()) > 1 or DEPARTURE_ROW.match(
                    stop["location"]
                ):
                    continue
                reference = (trip["evidence_id"], "scheduled_arrival", stop["location"])
                references.setdefault(reference, set()).add(stop["scheduled_at"])
    return references
