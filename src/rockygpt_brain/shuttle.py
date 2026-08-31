"""The one trusted database lookup needed for a next-shuttle answer."""

import os
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

        departure, trip = min(candidates, key=lambda candidate: candidate[0])
        return {
            "kind": "next_shuttle",
            "method": "deterministic_database_schedule_lookup",
            "currentTime": current.isoformat(timespec="seconds"),
            "departureAt": departure.isoformat(timespec="minutes"),
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


def asks_for_next_shuttle(messages: Sequence[object]) -> bool:
    """Recognize only a direct next-shuttle question in the latest user message."""
    for message in reversed(messages):
        role = getattr(message, "role", None)
        content = getattr(message, "content", "")
        if role == "user":
            normalized = str(content).casefold()
            return "shuttle" in normalized and ("next" in normalized or "when" in normalized)
    return False
