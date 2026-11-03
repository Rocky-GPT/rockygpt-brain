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
    applies to. Keep that uncertainty separate from a known date outside its interval: the result
    has no hours for the date (`hours` is null), and carries `listed_hours`, what the schedule
    itself lists for that weekday, so the student can be told what is listed and why it is not
    confirmed for the date.
  - A clock time the student asks about ("after 9pm", "at 10:30 p.m.") is read from their own
    words (asked_clock), and what the day's hours say for it is worked out like open now
    (fact_packet.open_at). A time asked with no day named is today's.
"""

import re
from datetime import date, timedelta
from typing import Any

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DAY_CHOICES = ("today", "tomorrow", *(name.lower() for name in WEEKDAYS))
MIDNIGHT = 24 * 60
EARLIEST = 5 * 60  # The first time of day a clock time is read as that day's.

# A clock time as students write one, with the word before it: "after 9pm", "past 10:30 p.m.",
# "at noon". A time with no am or pm ("after 9") is never read, since it could be either. A time
# from midnight to before 5am is the night after the day asked ("anything open after 1am?"), which
# one day's hours cannot answer, so it is not read either.
_CLOCK = re.compile(
    r"(?:\b([a-z]+)\s+(?:my\s+|the\s+)?)?"
    r"(?:\b(\d{1,2})(?::([0-5]\d))?\s*([ap])\.?\s*m\b\.?|\b(midnight|noon)\b)",
    re.IGNORECASE)
# What the word before the time asks: what is open from then on, or whether it is open then.
_RELATIONS = {"after": "after", "past": "after", "at": "at", "around": "at", "about": "at"}
# Words before a time that ask something else ("open until 10pm", "before 8am", "from 9pm to
# 11pm"): the time is not read, and the day's hours are said as they are.
_OTHER_RELATIONS = frozenset("before until till til by from to between and or than".split())
# Words that name a day or days other than today: a time asked with one is that day's.
_OTHER_DAY = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mondays|tuesdays|wednesdays|"
    r"thursdays|fridays|saturdays|sundays|tomorrow|tmrw|tmr|tmrow|weekends?|weekdays?|every|"
    r"usually|normally|always)\b", re.IGNORECASE)


def asked_clock(message: str) -> tuple[str, int] | None:
    """The one clock time a message asks about and how: ("after", 1260) for "food after 9pm",
    ("at", 600) for "open at 10am", in minutes from the start of the day. None when the message
    names no time, more than one, a time asked some other way ("until 10pm"), or a time in the
    night after the day (midnight to before 5am)."""
    found: set[tuple[str, int]] = set()
    for match in _CLOCK.finditer(message):
        before = (match[1] or "").casefold()
        if before in _OTHER_RELATIONS:
            return None
        if match[5]:
            minute = MIDNIGHT if match[5].casefold() == "midnight" else 12 * 60
        else:
            hour = int(match[2])
            if not 1 <= hour <= 12:
                return None
            minute = (hour % 12 + (12 if match[4].casefold() == "p" else 0)) * 60 + int(
                match[3] or 0)
        if not EARLIEST <= minute < MIDNIGHT:
            return None
        found.add((_RELATIONS.get(before, "at"), minute))
    return found.pop() if len(found) == 1 else None


def names_another_day(message: str) -> bool:
    """Whether a message names a day other than today, or days in general ("on weekends")."""
    return _OTHER_DAY.search(message) is not None


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
    if value.get("validity_absence"):
        out["validity_absence"] = value["validity_absence"]
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
    listed = (e for e in value.get("days", []) if isinstance(e, dict))
    entry = next((e for e in listed if e.get("day") == name), None)
    if applies:
        out["hours"] = entry.get("hours") if entry else None
    elif unverified:
        # Nothing says this schedule covers the date, so there are no hours for it. What the
        # schedule itself lists for that weekday is still a fact about the schedule: a writer may
        # tell a student what is listed and that its dates are not published, never that the office
        # is open then. A reviewed omission of that weekday holds whatever the dates are.
        out["hours"] = None
        out["listed_hours"] = entry.get("hours") if entry else None
    else:
        out["hours"] = None
        out["window"] = window(sources)
    if (applies or unverified) and entry and entry.get("status") == "not_published" \
            and entry.get("absence"):
        out["status"] = "not_published"
        out["absence"] = entry["absence"]
    if value.get("notes"):
        out["notes"] = list(value["notes"])
    return {"day": name, "date": day.isoformat(), "applies": applies, "value": out,
            **({"applicability": "unverified", "applicability_reason":
                "The seasonal schedule has no complete published date range; "
                "its applicability to this date is unverified."} if unverified else {})}
