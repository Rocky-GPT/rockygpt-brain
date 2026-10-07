"""Which day's hours a student asked about, worked out by code so a writer never has to.

A schedule fact holds the whole week. When the student names a day ("Saturday", "tomorrow"), the
Brain turns that into a real date on the campus clock, reads that weekday from each schedule, and
records the result as a derived fact. A writer is then handed that one day and does no calendar
arithmetic and no picking.

Rules, all in code:
  - "today" and "tomorrow" are the campus date and the one after; a weekday name is its next
    occurrence counting today (asked on a Saturday, "Saturday" is today).
  - A schedule says nothing about a date outside the validity window it was published for, so such a
    date gets no hours, only the window.
  - A weekday the schedule does not list has no hours here. The shared reader represents an
    unavailable weekday as null, never as "closed".
  - A seasonal schedule without a complete published interval cannot establish which dates it
    applies to. Keep that uncertainty separate from a known date outside its interval.
"""

from datetime import date, timedelta
from typing import Any

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DAY_CHOICES = ("today", "tomorrow", *(name.lower() for name in WEEKDAYS))


def target_date(day: str, today: date) -> date:
    """The date a day word means, counting from the campus date `today`."""
    if day == "today":
        return today
    if day == "tomorrow":
        return today + timedelta(days=1)
    index = [name.lower() for name in WEEKDAYS].index(day)
    return today + timedelta(days=(index - today.weekday()) % 7)


def _day(value: Any) -> date | None:
    """The calendar day of an ISO date or datetime text; None when there is none."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def covers(sources: list[dict[str, Any]], day: date) -> bool:
    """Whether a schedule published for these sources speaks for `day`.

    A source with no window (neither end published) speaks for every day. With a window it speaks
    for the days from `valid_from` through `valid_until`, either end open if unpublished.
    """
    for source in sources:
        start, end = _day(source.get("valid_from")), _day(source.get("valid_until"))
        if (start is None or start <= day) and (end is None or day <= end):
            return True
    return False


def window(sources: list[dict[str, Any]]) -> dict[str, str]:
    """The published validity window of these sources (the widest), as dates."""
    starts = [d for s in sources if (d := _day(s.get("valid_from")))]
    ends = [d for s in sources if (d := _day(s.get("valid_until")))]
    out: dict[str, str] = {}
    if starts:
        out["from"] = min(starts).isoformat()
    if ends:
        out["until"] = max(ends).isoformat()
    return out


def hours_on(value: dict[str, Any], sources: list[dict[str, Any]], day: date) -> dict[str, Any]:
    """One schedule's answer for `day`: {"schedule", "hours", "notes"?} plus whether it applies.

    `value` is a schedule fact's value exactly as the reader returned it. The result holds only what
    that schedule publishes for the weekday, or, for a date outside its window, the window itself.
    """
    name = WEEKDAYS[day.weekday()]
    out: dict[str, Any] = {"schedule": value.get("schedule")}
    if value.get("season"):
        out["season"] = value["season"]
    applies = covers(sources, day)
    unverified = bool(value.get("season")) and not any(
        _day(source.get("valid_from")) is not None
        and _day(source.get("valid_until")) is not None
        and covers([source], day) for source in sources)
    # Explicit bounds can already rule a date out. With no such exclusion, incomplete seasonal
    # dates mean "unverified", not that the requested date is outside a known semester.
    unverified = unverified and applies
    if unverified:
        applies = False
    if applies:
        listed = (e for e in value.get("days", []) if isinstance(e, dict))
        entry = next((e for e in listed if e.get("day") == name), None)
        out["hours"] = entry.get("hours") if entry else None
    else:
        out["hours"] = None
        out["window"] = window(sources)
    if value.get("notes"):
        out["notes"] = list(value["notes"])
    return {"day": name, "date": day.isoformat(), "applies": applies, "value": out,
            **({"applicability": "unverified", "applicability_reason":
                "The seasonal schedule has no complete published date range; "
                "its applicability to this date is unverified."} if unverified else {})}
