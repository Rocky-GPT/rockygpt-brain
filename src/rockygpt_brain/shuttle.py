"""Deterministic next-shuttle lookup from Ramapo's official Fall 2026 schedules."""

from collections.abc import Sequence
from datetime import datetime, time, timedelta
from math import ceil
from zoneinfo import ZoneInfo

CAMPUS_TIME_ZONE = ZoneInfo("America/New_York")
SCHEDULE_NAME = "Fall 2026"
SOURCE_CHECKED_AT = "2026-08-31"

WEEKDAY_URL = (
    "https://www.ramapo.edu/about/transportation-services/"
    "ramapo-roadrunner-express-shuttle/"
)
WEEKDAY_EXPRESS_URL = (
    "https://www.ramapo.edu/about/transportation-services/"
    "shuttle-mid-day-weekday-express-train-schedule/"
)
SATURDAY_URL = (
    "https://www.ramapo.edu/about/transportation-services/saturday-shuttle-schedule/"
)
SUNDAY_URL = (
    "https://www.ramapo.edu/about/transportation-services/sunday-shuttle-schedule/"
)

_WEEKDAY_FULL = (
    "7:00 AM", "8:25 AM", "10:15 AM", "11:35 AM", "12:20 PM", "2:05 PM",
    "3:10 PM", "3:50 PM", "4:40 PM", "6:10 PM", "8:20 PM", "9:40 PM",
)
_WEEKDAY_EXPRESS = (
    "7:00 AM", "7:25 AM", "7:50 AM", "8:20 AM", "8:40 AM", "9:05 AM",
    "9:45 AM", "10:40 AM", "11:45 AM", "12:45 PM", "1:40 PM", "2:35 PM",
    "2:55 PM", "3:25 PM", "3:50 PM", "4:10 PM", "4:45 PM", "5:30 PM",
)
_SATURDAY = (
    "9:00 AM", "9:55 AM", "11:00 AM", "12:50 PM", "1:40 PM", "3:00 PM",
    "4:40 PM", "5:25 PM", "6:50 PM", "7:30 PM", "8:55 PM", "9:55 PM",
)
_SUNDAY = (
    "10:00 AM", "11:00 AM", "12:00 PM", "1:00 PM", "2:00 PM", "3:15 PM",
    "3:50 PM", "5:45 PM", "6:55 PM",
)


def _clock(value: str) -> time:
    return datetime.strptime(value, "%I:%M %p").time()


def _daily_schedule(weekday: int) -> list[tuple[time, str, str]]:
    if weekday < 5:
        trips = [(_clock(value), "weekday full service", WEEKDAY_URL) for value in _WEEKDAY_FULL]
        trips += [
            (_clock(value), "weekday train express", WEEKDAY_EXPRESS_URL)
            for value in _WEEKDAY_EXPRESS
        ]
        return sorted(trips, key=lambda trip: trip[0])
    if weekday == 5:
        return [(_clock(value), "Saturday service", SATURDAY_URL) for value in _SATURDAY]
    return [(_clock(value), "Sunday service", SUNDAY_URL) for value in _SUNDAY]


def next_shuttle(now: datetime | None = None) -> dict[str, str | int]:
    """Return the next scheduled departure from Ramapo's Bradley Center."""
    current = now or datetime.now(CAMPUS_TIME_ZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CAMPUS_TIME_ZONE)
    else:
        current = current.astimezone(CAMPUS_TIME_ZONE)

    for days_ahead in range(8):
        service_date = current.date() + timedelta(days=days_ahead)
        for departure_time, service, source_url in _daily_schedule(service_date.weekday()):
            departure = datetime.combine(service_date, departure_time, CAMPUS_TIME_ZONE)
            if departure < current:
                continue
            return {
                "kind": "next_shuttle",
                "method": "deterministic_schedule_lookup",
                "currentTime": current.isoformat(timespec="seconds"),
                "departureAt": departure.isoformat(timespec="minutes"),
                "departureTime": departure.strftime("%I:%M %p").lstrip("0"),
                "minutesUntil": ceil((departure - current).total_seconds() / 60),
                "origin": "Bradley Center, Ramapo College",
                "service": f"{SCHEDULE_NAME} {service}",
                "sourceTitle": "Ramapo College Transportation Services",
                "sourceUrl": source_url,
                "sourceCheckedAt": SOURCE_CHECKED_AT,
                "note": "Official shuttle times are approximate.",
            }
    raise RuntimeError("No shuttle departure found")


def asks_for_next_shuttle(messages: Sequence[object]) -> bool:
    """Recognize only a direct next-shuttle question in the latest user message."""
    for message in reversed(messages):
        role = getattr(message, "role", None)
        content = getattr(message, "content", "")
        if role == "user":
            normalized = str(content).casefold()
            return "shuttle" in normalized and ("next" in normalized or "when" in normalized)
    return False
