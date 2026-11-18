"""When a question asks about, picked by the read (triage.py), turned into campus days by code.

Tools review 11-18 (plan: "let the question reader pick the time, not word rules", Dan's ok
11-18): the time words of events.py were hand-written guesses ("wen is thanksgivin brk" and
"anything fun after finals" slipped past them, and each miss was patched with one more rule).
The read that already runs on every message now also picks, from fixed lists, when the student
means, which kind of calendar date they want, which term, and home or away games. It never
writes a date: code here turns the picks into real days, so all date math stays in code.

A read that failed, or picked no time, leaves the usual window: the next DEFAULT_DAYS days. No
word rules stand in for a missing read (old vs new on the steady ruler's open half, 11-18: 18 of
60 right with the picks, 16 with the word rules; 45 wrong answers against 47).
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

DEFAULT_DAYS = 14
DEFAULT_LABEL = f"the next {DEFAULT_DAYS} days"

# The read's lists. The first of each is what a tie or a pick off the list falls back to.
WHEN = ("none", "today", "tonight", "tomorrow", "weekday", "next_weekday", "this_week",
        "next_week", "this_weekend", "next_weekend", "this_month", "next_month", "month",
        "date", "break", "rest_of_term")
WEEKDAYS = ("none", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
            "sunday")
BREAKS = ("none", "thanksgiving", "winter", "spring", "summer", "fall")
SIDES = ("during", "before", "after")
PARTS = ("none", "morning", "afternoon", "evening")
# The academic calendar's kinds of date (academic_calendar.py), named as its entries name them.
DATE_KINDS = ("none", "add_drop_deadline", "withdrawal_deadline", "tuition_refund_deadline",
              "grading_option_deadline", "finals", "grades_due", "break", "classes_begin",
              "classes_end")
TERMS = ("none", "this", "next", "fall", "spring", "summer", "winter")
HOME_AWAY = ("any", "home", "away")

PROMPT = """- time: when the question asks about, and what kind of date, picked from the lists;
  never work out a date yourself. when: today, tonight (this evening or tonight), tomorrow,
  weekday (a day of the week: "fri", "this saturday"), next_weekday ("next friday"), this_week,
  next_week, this_weekend, next_weekend, this_month, next_month, month (a month by name with no
  day: "in december"), date (a month and day: "dec 3rd", "12/3"), break (during, before or
  after a break: "over thanksgiving", "after winter break"), rest_of_term (later this
  semester: "before finals", "the rest of the semester", "end of the semester"), else none.
  Most questions name no time: none. weekday: the day named, else none. month and day: the
  month (1 to 12) and day of the month named, else 0. break_name: thanksgiving, winter, spring,
  summer or fall when a break is named, else none; side: before, after, or during (the break's
  own days, and when none is named). part_of_day: morning, afternoon or evening (evening for
  "night"), else none. date_kind: the kind of academic-calendar date asked about:
  add_drop_deadline (last day to add or drop a class), withdrawal_deadline,
  tuition_refund_deadline, grading_option_deadline (pass/fail, audit, incomplete), finals,
  grades_due, break (when a break or holiday is), classes_begin, classes_end, else none.
  term: the semester asked about: this (this or the current semester), next (next semester),
  fall, spring, summer, winter, else none. home_away: home or away when the question asks only
  for home or only for away games, else any."""

SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["when", "weekday", "month", "day", "break_name", "side", "part_of_day",
                 "date_kind", "term", "home_away"],
    "properties": {
        "when": {"type": "string", "enum": list(WHEN)},
        "weekday": {"type": "string", "enum": list(WEEKDAYS)},
        "month": {"type": "integer"},
        "day": {"type": "integer"},
        "break_name": {"type": "string", "enum": list(BREAKS)},
        "side": {"type": "string", "enum": list(SIDES)},
        "part_of_day": {"type": "string", "enum": list(PARTS)},
        "date_kind": {"type": "string", "enum": list(DATE_KINDS)},
        "term": {"type": "string", "enum": list(TERMS)},
        "home_away": {"type": "string", "enum": list(HOME_AWAY)},
    },
}


@dataclass(frozen=True, slots=True)
class AskedTime:
    """The read's time picks. The defaults are "no time named"."""

    when: str = "none"
    weekday: str = "none"
    month: int = 0
    day: int = 0
    break_name: str = "none"
    side: str = "during"
    part_of_day: str = "none"
    date_kind: str = "none"
    term: str = "none"
    home_away: str = "any"

    def as_trace(self) -> dict[str, Any]:
        """Only the picks that say something, for the turn's trace."""
        blank = AskedTime()
        return {name: getattr(self, name) for name in SCHEMA["required"]
                if getattr(self, name) != getattr(blank, name)}

    @property
    def span(self) -> tuple[str, str, int, int, str, str, str]:
        """The picks that together say one window of days: voted on as one."""
        return (self.when, self.weekday, self.month, self.day, self.break_name, self.side,
                self.part_of_day)


def of(arguments: Any) -> AskedTime | None:
    """The read's time picks, each checked against its list; None when the shape is wrong.
    A month or day out of range counts as not named."""
    if not isinstance(arguments, Mapping):
        return None
    lists = {"when": WHEN, "weekday": WEEKDAYS, "break_name": BREAKS, "side": SIDES,
             "part_of_day": PARTS, "date_kind": DATE_KINDS, "term": TERMS,
             "home_away": HOME_AWAY}
    if any(arguments.get(name) not in allowed for name, allowed in lists.items()):
        return None
    month, day = arguments.get("month"), arguments.get("day")
    if not isinstance(month, int) or not isinstance(day, int) or isinstance(month, bool) \
            or isinstance(day, bool):
        return None
    month = month if 1 <= month <= 12 else 0
    day = day if 1 <= day <= 31 else 0
    return AskedTime(**{name: str(arguments[name]) for name in lists}, month=month, day=day)


def vote(picks: Sequence[AskedTime]) -> AskedTime | None:
    """What most reads pick. The window's picks count as one (a read's "friday" and another's
    "evening" never mix into a window neither read gave); the kind of date, the term and home or
    away are each voted alone. A tie goes to "no time named", the safe default."""
    if not picks:
        return None
    half = len(picks) // 2

    def most(values: Sequence[Any], blank: Any) -> Any:
        value, count = Counter(values).most_common(1)[0]
        return value if count > half else blank

    blank = AskedTime()
    span = most([p.span for p in picks], blank.span)
    when, weekday, month, day, break_name, side, part = span
    return AskedTime(when, weekday, month, day, break_name, side, part,
                     most([p.date_kind for p in picks], blank.date_kind),
                     most([p.term for p in picks], blank.term),
                     most([p.home_away for p in picks], blank.home_away))


def _start(day: date, now: datetime) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=now.tzinfo)


def _end(day: date, now: datetime) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=now.tzinfo)


def default_window(now: datetime) -> tuple[datetime, datetime, str]:
    """The next DEFAULT_DAYS days: what a question naming no time asks about."""
    return now, _end(now.date() + timedelta(days=DEFAULT_DAYS), now), DEFAULT_LABEL


def rest_of_term(now: datetime) -> tuple[datetime, datetime, str]:
    """To the end of the semester in session: December in the fall, June in the spring."""
    today = now.date()
    last = date(today.year, 12, 31) if today.month >= 8 else date(today.year, 6, 30)
    return now, _end(last, now), "the rest of the semester"


def _month_span(first: date) -> date:
    return date(first.year + (first.month == 12), first.month % 12 + 1, 1) - timedelta(days=1)


def _break_window(asked: AskedTime, now: datetime,
                  breaks: Sequence[tuple[str, date, date]]) -> tuple[datetime, datetime, str]:
    """A named break's own days, the days until it begins, or the two weeks after it ends: the
    next break by that name on the academic calendar (`breaks`, each (name, first day, last
    day)) that has not ended. A break the calendar does not date is the rest of the semester."""
    today = now.date()
    found = next(((name, first, last) for name, first, last in sorted(breaks, key=lambda b: b[1])
                  if asked.break_name in name.casefold() and last >= today), None)
    if found is None:
        return rest_of_term(now)
    name, first, last = found
    said = name.split(" (")[0].strip()
    if asked.side == "before":
        return now, _end(max(first - timedelta(days=1), today), now), f"before {said}"
    if asked.side == "after":
        begins = last + timedelta(days=1)
        return (max(now, _start(begins, now)),
                _end(begins + timedelta(days=DEFAULT_DAYS - 1), now), f"after {said}")
    return max(now, _start(first, now)), _end(last, now), f"over {said}"


def _days(asked: AskedTime, now: datetime,
          breaks: Sequence[tuple[str, date, date]]) -> tuple[datetime, datetime, str]:
    today = now.date()
    when = asked.when
    if when == "tonight":
        return max(now, datetime.combine(today, time(17, 0), tzinfo=now.tzinfo)), \
            _end(today, now), "tonight"
    if when == "today":
        return _start(today, now), _end(today, now), "today"
    if when == "tomorrow":
        day = today + timedelta(days=1)
        return _start(day, now), _end(day, now), "tomorrow"
    if when in ("weekday", "next_weekday") and asked.weekday != "none":
        n = WEEKDAYS.index(asked.weekday) - 1
        ahead = (n - today.weekday()) % 7
        if when == "next_weekday" and ahead == 0:
            ahead = 7
        day = today + timedelta(days=ahead)
        return _start(day, now), _end(day, now), f"{asked.weekday.title()}, {day:%B} {day.day}"
    if when in ("this_weekend", "next_weekend"):
        friday = today + timedelta(days=(4 - today.weekday()) % 7)
        if today.weekday() >= 5:  # Saturday or Sunday: this weekend is now.
            friday = today - timedelta(days=today.weekday() - 4)
        if when == "next_weekend":
            friday += timedelta(days=7)
        start = max(now, datetime.combine(friday, time(17, 0), tzinfo=now.tzinfo))
        return start, _end(friday + timedelta(days=2), now), when.replace("_", " ")
    if when == "this_week":
        # On a Sunday the week ahead is meant, not the hours left in it.
        last = today + timedelta(days=6 - today.weekday() + (7 if today.weekday() == 6 else 0))
        return now, _end(last, now), "this week"
    if when == "next_week":
        monday = today + timedelta(days=7 - today.weekday())
        return _start(monday, now), _end(monday + timedelta(days=6), now), "next week"
    if when == "date" and asked.month and asked.day:
        try:
            day = date(today.year, asked.month, asked.day)
            if day < today:
                day = date(today.year + 1, asked.month, asked.day)
        except ValueError:
            return default_window(now)
        return _start(day, now), _end(day, now), f"{day:%A, %B} {day.day}"
    if when == "this_month":
        return now, _end(_month_span(today.replace(day=1)), now), "this month"
    if when == "next_month":
        first = _month_span(today.replace(day=1)) + timedelta(days=1)
        return _start(first, now), _end(_month_span(first), now), f"in {first:%B}"
    if when == "month" and asked.month:
        year = today.year if asked.month >= today.month else today.year + 1
        first = date(year, asked.month, 1)
        return max(now, _start(first, now)), _end(_month_span(first), now), f"in {first:%B}"
    if when == "break" and asked.break_name != "none":
        return _break_window(asked, now, breaks)
    if when == "rest_of_term":
        return rest_of_term(now)
    return default_window(now)


_PART_HOURS = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}


def window(asked: AskedTime | None, now: datetime,
           breaks: Sequence[tuple[str, date, date]] = ()) -> tuple[datetime, datetime, str]:
    """The span of campus time the picks ask about, and its name; the next DEFAULT_DAYS days
    when they name no time, or there was no read. A part of the day ("friday night") narrows a
    one-day window. `breaks` are the academic calendar's (name, first day, last day)."""
    if asked is None:
        return default_window(now)
    start, end, label = _days(asked, now, breaks)
    if (asked.part_of_day != "none" and label != "tonight"
            and start.date() == end.date()):
        first, last = _PART_HOURS[asked.part_of_day]
        day = start.date()
        start = max(start, datetime.combine(day, time(first, 0), tzinfo=now.tzinfo))
        if last < 24:
            end = datetime.combine(day, time(last, 0), tzinfo=now.tzinfo)
        return start, end, f"{label}, {asked.part_of_day}"
    return start, end, label


_MONTH_WORDS = ("january jan", "february feb", "march mar", "april apr", "may", "june jun",
                "july jul", "august aug", "september sept sep", "october oct",
                "november nov", "december dec")
_BREAK_WORDS = "break breaks recess vacation holiday holidays"
_TERM_WORDS = "finals final exams exam end semester term"


def said_words(asked: AskedTime | None) -> frozenset[str]:
    """The words that name the picked time ("december", "thanksgiving break", "finals"): they say
    when, not what an event is about (events.asked_topic). None when there was no read."""
    if asked is None:
        return frozenset()
    words = []
    if asked.when in ("month", "date") and 1 <= asked.month <= 12:
        words.append(_MONTH_WORDS[asked.month - 1])
    if asked.when == "break" or asked.date_kind == "break":
        words += [asked.break_name, _BREAK_WORDS]
    if asked.when == "rest_of_term" or asked.date_kind == "finals":
        words.append(_TERM_WORDS)
    return frozenset(" ".join(words).split()) - {"none"}


# The current turn's picks, set once the read is in (engine._read_then_answer) and read where a
# time is turned into days, so each answer path need not pass them along. None: no read, and the
# question gets the usual window.
ASKED: ContextVar[AskedTime | None] = ContextVar("asked_time", default=None)
