"""Meal periods from a dining venue's schedule text, and what is open, served or next at a time.

A dining venue publishes each weekday as one line of text: labelled periods joined by "; "
("Breakfast: 08:00 AM - 10:30 AM; Lunch: 11:00 AM - 02:00 PM"), a single unlabelled range
("07:30 AM - 03:00 PM"), "Closed", or the collector's "Hours unavailable". Code reads the line into
periods so a writer is never asked to do clock arithmetic: which period is on now, which comes
next, and when each opens and closes.

Only text that is wholly periods is read. Anything else (a note beside the times, a range that
runs past midnight, "Hours unavailable") is left unread, because a wrong "open" or "closed" is
worse than none. Intervals are half-open: a period is open at its opening minute and closed at
its closing minute, and 12:00 AM as a closing time is midnight.
"""

import re
from dataclasses import dataclass

_TIME = r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?"
_RANGE = re.compile(rf"{_TIME}\s*(?:-|–|—|to|until)\s*{_TIME}", re.IGNORECASE)
# A label is words before the colon that precedes a clock time: it cannot start with a digit.
_LABELLED = re.compile(r"^(?P<label>[^\d:][^:]*?)\s*:\s*(?P<range>\d.*)$")
MIDNIGHT = 24 * 60


@dataclass(frozen=True)
class Period:
    """One stretch of service, in minutes after midnight (`end` may be 1440, midnight)."""

    label: str | None
    start: int
    end: int


def _minutes(hour: str, minute: str | None, half: str) -> int:
    return (int(hour) % 12 + (12 if half.casefold() == "p" else 0)) * 60 + int(minute or 0)


def parse(text: object) -> list[Period] | None:
    """The periods a day's schedule text names: [] when it says closed, None when it is not
    wholly periods the code can read."""
    if not isinstance(text, str):
        return None
    flat = " ".join(text.split())
    if not flat:
        return None
    if re.fullmatch(r"closed(?: \(seasonal closure\))?", flat, re.IGNORECASE):
        return []
    periods: list[Period] = []
    for segment in (part.strip() for part in flat.split(";")):
        if not segment:
            return None
        labelled = _LABELLED.match(segment)
        label = labelled["label"].strip() if labelled else None
        found = _RANGE.fullmatch(labelled["range"].strip() if labelled else segment)
        if found is None:
            return None
        start = _minutes(found[1], found[2], found[3])
        end = _minutes(found[4], found[5], found[6]) or MIDNIGHT
        if end <= start:
            return None
        periods.append(Period(label, start, end))
    return periods


def clock_text(minutes: int) -> str:
    """8:00am, 12:30pm, 12:00am (midnight): the way the office pages write a time."""
    minutes %= MIDNIGHT
    hour, minute = divmod(minutes, 60)
    return f"{hour % 12 or 12}:{minute:02d}{'pm' if hour >= 12 else 'am'}"


def named(periods: list[Period]) -> list[dict[str, str]]:
    """The periods as plain rows a writer can say: label (when there is one), opens, closes."""
    return [{**({"label": p.label} if p.label else {}), "opens": clock_text(p.start),
             "closes": clock_text(p.end)} for p in periods]


def at(periods: list[Period], minute: int) -> Period | None:
    """The period in service at this minute, or None."""
    return next((p for p in periods if p.start <= minute < p.end), None)


def after(periods: list[Period], minute: int) -> Period | None:
    """The first period that opens after this minute today, or None."""
    return min((p for p in periods if p.start > minute), key=lambda p: p.start, default=None)


def gaps(periods: list[Period]) -> list[dict[str, str]]:
    """The stretches between one period's close and the next one's opening, in day order.

    Periods that meet (lunch to 2:00pm, the next from 2:00pm) leave no gap. An empty list is a
    day that is open straight through from its first opening to its last closing."""
    ordered = sorted(periods, key=lambda p: p.start)
    found = []
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if later.start > earlier.end:
            found.append({**({"after": earlier.label} if earlier.label else {}),
                          "closed_from": clock_text(earlier.end),
                          "reopens_at": clock_text(later.start),
                          **({"before": later.label} if later.label else {})})
    return found
