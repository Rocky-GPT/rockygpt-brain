"""Which day's hours a student asked about, worked out by code so a writer never has to.

A schedule fact holds the whole week. When the student names a day ("Saturday", "tomorrow"), the
Brain turns that into a real date on the campus clock, reads that weekday from each schedule, and
records the result as a derived fact. A writer is then handed that one day and does no calendar
arithmetic and no picking.

Rules, all in code:
  - "today" and "tomorrow" are the campus date and the one after; a weekday name is its next
    occurrence counting today (asked on a Saturday, "Saturday" is today); a date the student names
    ("Friday, November 27") is that date, given as YYYY-MM-DD.
  - A schedule's special hours ("Special Hours: Nov 25 - Nov 28: CLOSED; Nov 29: 4:00pm-12:00am",
    as the data reader keeps them in its note) are its hours on those dates, in place of the
    weekday's.
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
# The day field of a lookup: one of DAY_CHOICES, or the date itself.
DAY_PATTERN = "^(?:" + "|".join(DAY_CHOICES) + r"|\d{4}-\d{2}-\d{2})$"
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def asked_day(value: str | None) -> str | None:
    """A lookup's day, checked: a day word, or a real calendar date. Anything else is refused."""
    if value is None:
        return None
    if value in DAY_CHOICES:
        return value
    if _ISO_DAY.match(value):
        date.fromisoformat(value)  # Raises for a date that does not exist (2026-02-30).
        return value
    raise ValueError(f"Not a day: {value!r}")
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
    """The date a day word means, counting from the campus date `today`; a date is itself."""
    if _ISO_DAY.match(day):
        return date.fromisoformat(day)
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


_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_SPECIAL_DATE = r"([A-Za-z]{3,9})\.? (\d{1,2})"
# One dated line of a note's special hours, as the data reader writes them: "Nov 25 - Nov 28:
# CLOSED", "Sept. 8: 4:00pm-12:00am", each ended by "; ", by ". " or by the note's end.
_SPECIAL_ENTRY = re.compile(
    rf"{_SPECIAL_DATE}(?: - {_SPECIAL_DATE})?: ([^;]+?)(?:; |\.(?:\s|$)|$)")


def _month_day(month: str, day: str, sources: list[dict[str, Any]]) -> date | None:
    """A month and day with no year, placed in the year of the schedule's published window."""
    number = _MONTHS.get(month[:3].casefold())
    bounds = window(sources)
    if number is None or not bounds:
        return None
    years = sorted({int(bounds[end][:4]) for end in ("from", "until") if end in bounds})
    for year in years:
        try:
            found = date(year, number, int(day))
        except ValueError:
            return None
        if covers(sources, found):
            return found
    return None


def special_hours(notes: Any, sources: list[dict[str, Any]], day: date) -> dict[str, str] | None:
    """The special hours a schedule's note gives for `day`: {"dates", "hours"} as published, or
    None. The dates carry no year, so each is placed inside the schedule's own window; a date that
    cannot be placed there is not read."""
    for note in notes if isinstance(notes, list) else [notes]:
        at = note.find("Special Hours: ") if isinstance(note, str) else -1
        if at < 0:
            continue
        rest = note[at + len("Special Hours: "):]
        while parts := _SPECIAL_ENTRY.match(rest):
            first = _month_day(parts[1], parts[2], sources)
            last = _month_day(parts[3], parts[4], sources) if parts[3] else first
            if first and last and first <= day <= last:
                return {"dates": parts[0].split(": ", 1)[0], "hours": parts[5].strip()}
            if not parts[0].endswith("; "):
                break
            rest = rest[parts.end():]
    return None


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
    special = special_hours(value.get("notes"), sources, day) if applies else None
    if special:
        # The schedule's own special hours for this date, in place of the weekday's.
        out["hours"] = special["hours"]
        out["special_hours"] = special["dates"]
    elif applies:
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
    if (applies or unverified) and not special and entry \
            and entry.get("status") == "not_published" and entry.get("absence"):
        out["status"] = "not_published"
        out["absence"] = entry["absence"]
    if value.get("notes"):
        out["notes"] = list(value["notes"])
    return {"day": name, "date": day.isoformat(), "applies": applies, "value": out,
            **({"applicability": "unverified", "applicability_reason":
                "The seasonal schedule has no complete published date range; "
                "its applicability to this date is unverified."} if unverified else {})}
