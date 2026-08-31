"""The one trusted database lookup needed for a next-shuttle answer."""

import os
import re
import ssl
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from math import ceil
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import asyncpg  # type: ignore[import-untyped]

CAMPUS_TIME_ZONE = ZoneInfo(os.getenv("CAMPUS_TIME_ZONE", "America/New_York"))

_SHUTTLE_TRIPS_SQL = """
SELECT
    t.id::text AS trip_id,
    t.source_record_key,
    t.departure,
    t.arrival,
    t.collected_at,
    t.valid_from,
    t.valid_until,
    t.content_hash,
    r.name AS route_name,
    r.service_day,
    d.version AS dataset_version,
    d.activated_at AS dataset_activated_at,
    s.title AS source_title,
    s.canonical_url AS source_url,
    s.trust_tier AS source_trust_tier
FROM rockygpt_v2.shuttle_trips AS t
JOIN rockygpt_v2.shuttle_routes AS r ON r.id = t.route_id
JOIN rockygpt_v2.dataset_versions AS d ON d.id = t.dataset_version_id
JOIN rockygpt_v2.sources AS s ON s.id = t.source_id
WHERE d.status = 'active'
"""


def _database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for shuttle answers")
    parts = urlsplit(value)
    query = urlencode(
        [
            (key, item)
            for key, item in parse_qsl(parts.query)
            if key not in {"sslmode", "channel_binding"}
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


async def load_shuttle_trips() -> list[dict[str, object]]:
    """Read active shuttle trips and their provenance from the trusted database."""
    connection = await asyncpg.connect(_database_url(), ssl=ssl.create_default_context())
    try:
        return [dict(row) for row in await connection.fetch(_SHUTTLE_TRIPS_SQL)]
    finally:
        await connection.close()


def _service_day(value: date) -> str:
    if value.weekday() < 5:
        return "weekday"
    return "saturday" if value.weekday() == 5 else "sunday"


def _clock(value: object) -> time | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.replace(" ", ""), "%I:%M%p").time()
    except ValueError:
        return None


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, date | datetime) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def next_shuttle(
    trips: Sequence[Mapping[str, object]], now: datetime | None = None
) -> dict[str, object]:
    """Select the next database trip using the current campus time."""
    current = now or datetime.now(CAMPUS_TIME_ZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CAMPUS_TIME_ZONE)
    else:
        current = current.astimezone(CAMPUS_TIME_ZONE)

    for days_ahead in range(8):
        service_date = current.date() + timedelta(days=days_ahead)
        candidates: list[tuple[datetime, Mapping[str, object]]] = []
        for trip in trips:
            if trip.get("service_day") != _service_day(service_date):
                continue
            valid_from = trip.get("valid_from")
            valid_until = trip.get("valid_until")
            if isinstance(valid_from, date) and service_date < valid_from:
                continue
            if isinstance(valid_until, date) and service_date > valid_until:
                continue
            departure_time = _clock(trip.get("departure"))
            if departure_time is None:
                continue
            departure = datetime.combine(service_date, departure_time, CAMPUS_TIME_ZONE)
            if departure >= current:
                candidates.append((departure, trip))
        if not candidates:
            continue

        departure, trip = min(
            candidates,
            key=lambda candidate: (
                candidate[0],
                str(candidate[1].get("route_name", "")),
                str(candidate[1].get("trip_id", "")),
            ),
        )
        if service_date == current.date():
            departure_day = "today"
        elif service_date == current.date() + timedelta(days=1):
            departure_day = "tomorrow"
        else:
            departure_day = departure.strftime("%A")
        return {
            "kind": "next_shuttle",
            "method": "deterministic_database_schedule_lookup",
            "currentTime": current.isoformat(timespec="seconds"),
            "departureAt": departure.isoformat(timespec="minutes"),
            "departureDate": service_date.isoformat(),
            "departureDay": departure_day,
            "departureTime": departure.strftime("%I:%M %p").lstrip("0"),
            "minutesUntil": ceil((departure - current).total_seconds() / 60),
            "route": _text(trip.get("route_name")),
            "arrival": _text(trip.get("arrival")),
            "datasetVersion": _text(trip.get("dataset_version")),
            "datasetActivatedAt": _iso(trip.get("dataset_activated_at")),
            "tripId": _text(trip.get("trip_id")),
            "sourceRecordKey": _text(trip.get("source_record_key")),
            "contentHash": _text(trip.get("content_hash")),
            "collectedAt": _iso(trip.get("collected_at")),
            "sourceTitle": _text(trip.get("source_title")),
            "sourceUrl": _text(trip.get("source_url")),
            "sourceTrustTier": _text(trip.get("source_trust_tier")),
        }
    raise RuntimeError("The active dataset has no upcoming shuttle trip")


async def next_shuttle_from_database(now: datetime | None = None) -> dict[str, object]:
    return next_shuttle(await load_shuttle_trips(), now)


def render_next_shuttle_answer(fact: Mapping[str, object]) -> str:
    """Render only trusted values, leaving no factual fields for a model to alter."""
    departure_time = str(fact["departureTime"])
    departure_day = str(fact["departureDay"])
    minutes_until = fact["minutesUntil"]
    if not isinstance(minutes_until, int):
        raise ValueError("minutesUntil must be an integer")
    route = _text(fact.get("route"))
    arrival = _text(fact.get("arrival"))

    route_text = f" on **{route}**" if route else ""
    answer = (
        f"The next shuttle{route_text} is scheduled to depart **{departure_day} at "
        f"{departure_time}**."
    )
    if minutes_until == 0:
        answer += " It is due now."
    elif minutes_until == 1:
        answer += " That is in **1 minute**."
    else:
        answer += f" That is in **{minutes_until} minutes**."
    if arrival:
        answer += f" Its scheduled arrival is **{arrival}**."
    return answer


_OUT_OF_SCOPE_SHUTTLE_PATTERNS = (
    r"\b(?:tomorrow|yesterday|tonight)\b",
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\b(?:schedule|timetable|times|all|every|list|multiple|departures|trips)\b",
    r"\bshuttles\b",
    r"\bnext\s+(?:two|three|four|\d+)\b",
    r"\b(?:last|previous|earlier|past|already)\b",
    r"\b(?:compare|comparison|versus|vs|faster|sooner)\b",
    r"\b(?:destination|where|which route|what route|stop)\b",
    r"\b(?:going|headed)\s+to\b",
    r"\bshuttle\s+to\s+(?!leave\b|depart\b)",
    r"\b(?:to|from|toward|towards|for)\s+(?!leave\b|depart\b)",
    r"\b(?:arrive|arrival)\b",
    r"\b(?:after|before|between)\b",
    r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
)


def asks_for_next_shuttle(messages: Sequence[object]) -> bool:
    """Recognize only an immediate, singular next-shuttle question."""
    for message in reversed(messages):
        role = getattr(message, "role", None)
        content = getattr(message, "content", "")
        if role == "user":
            normalized = " ".join(re.findall(r"[a-z0-9']+", str(content).casefold()))
            if "shuttle" not in normalized:
                return False
            if any(re.search(pattern, normalized) for pattern in _OUT_OF_SCOPE_SHUTTLE_PATTERNS):
                return False
            if "next shuttle" in normalized:
                return True
            if "another shuttle" in normalized:
                return bool(
                    re.search(r"\b(?:coming|soon|due|when|is there|what time)\b", normalized)
                )
            return bool(re.search(r"\bshuttle (?:coming|due)(?: up| soon)?\b", normalized))
    return False
