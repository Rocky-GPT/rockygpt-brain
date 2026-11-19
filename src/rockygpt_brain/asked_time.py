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

Keep the student's conditions (plan 11-19, Dan chose "Build Steps 0 to 2 on dev"): the picks are
the one record of what was asked. Beside one stretch of days they hold a range ("wed thru sun",
"dec 1-4": `through`), more days ("mon and tues", "this week or next": `also`), "the next 3 days"
(`coming_days`), a clock limit ("til 2", "after 9", "around lunch", "between 12 and 3": `clock`)
and, in the student's own words, any other condition no list holds ("for commuters": `other`).
When most reads saw a condition but did not agree on it, the vote says so (`unsure`) instead of
falling to "no condition" in silence. Each list lookup reports which of these it used
(conditions.py), and an answer says the conditions back.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time, timedelta
from typing import Any

DEFAULT_DAYS = 14
DEFAULT_LABEL = f"the next {DEFAULT_DAYS} days"

# The read's lists. The first of each is what a tie or a pick off the list falls back to.
WHEN = ("none", "today", "tonight", "tomorrow", "weekday", "next_weekday", "this_week",
        "next_week", "this_weekend", "next_weekend", "this_month", "next_month", "month",
        "date", "break", "rest_of_term", "coming_days")
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
# How a clock time limits what is asked about, and the half of the day a student said.
RELATIONS = ("none", "before", "after", "around", "between")
HALVES = ("none", "am", "pm")
# The most of each kept: more days beside the first, phrases of other conditions, days ahead.
MAX_ALSO = 2
MAX_OTHER = 3
MAX_DAYS_AHEAD = 90
# A range longer than this is not one a student names ("dec 1-4"): it is said as not placed.
MAX_RANGE_DAYS = 120

PROMPT = """- time: when the question asks about, and what kind of date, picked from the lists;
  never work out a date yourself. when: today, tonight (this evening or tonight), tomorrow,
  weekday (a day of the week: "fri", "this saturday"), next_weekday ("next friday"), this_week,
  next_week, this_weekend, next_weekend, this_month, next_month, month (a month by name with no
  day: "in december"), date (a month and day: "dec 3rd", "12/3"), break (during, before or
  after a break: "over thanksgiving", "after winter break"), rest_of_term (later this
  semester: "before finals", "the rest of the semester", "end of the semester"), coming_days
  ("the next 3 days", "in the next 72 hrs"), else none. Most questions name no time: none. A
  clock time with no day named ("anything after 5") is today. weekday: the day named, else
  none. month and day: the month (1 to 12) and day of the month named, else 0. days_ahead: the
  number of days with coming_days ("the next 3 days": 3), else 0. through: the last day when
  the student gives a range that starts on the day picked ("wed thru sun": weekday sunday; "dec
  1-4": month 12, day 4), else weekday none, month 0, day 0. also: up to 2 more days or weeks
  named beside the first, each with its own when, weekday, month and day ("mon and tues":
  when weekday, weekday monday, also one weekday tuesday; "this week or next": also one
  next_week), else empty. break_name: thanksgiving, winter, spring, summer or fall when a
  break is named, else none; side: before, after, or during (the break's own days, and when
  none is named). part_of_day: morning, afternoon or evening (evening for "night"), else none.
  clock: a clock time that limits what is asked: relation before ("til 2", "b4 noon", "by 5",
  "have to leave at 1"), after ("after my 5pm class", "past 6"), around ("around 8", "around
  lunch"), between ("between 12 and 3", "from noon to 3"), else none; hour (1 to 12) and minute
  as the student says them (noon 12, lunch about 12, dinner about 6), half am or pm when the
  student or the words say it ("8 in the morning", "tonight"), else none; to_hour, to_minute
  and to_half the end of a between, else 0 and none. date_kind: the kind of academic-calendar
  date asked about: add_drop_deadline (last day to add or drop a class), withdrawal_deadline,
  tuition_refund_deadline, grading_option_deadline (pass/fail, audit, incomplete), finals,
  grades_due, break (when a break or holiday is), classes_begin, classes_end, else none.
  term: the semester asked about: this (this or the current semester), next (next semester),
  fall, spring, summer, winter, else none. home_away: home or away when the question asks only
  for home or only for away games, else any. about: what kind of event, activity or thing the
  student wants, in a few plain words from the message with spelling fixed ("comedy shows",
  "free food", "chess club", "black solidarity week"); empty when they ask only what is on
  ("anything fun", "whats going on", "anything happening sat night") or say only who they are,
  where they are or how they feel ("freshman stuck in the dorms"). other: up to 3 short
  phrases copied word for word from the message, each a condition on the events or games
  themselves that nothing above holds ("for commuters", "free to get in", "online", "in
  person"); never who the student is, where they are or how they feel; else empty."""

_DAY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["weekday", "month", "day"],
    "properties": {"weekday": {"type": "string", "enum": list(WEEKDAYS)},
                   "month": {"type": "integer"}, "day": {"type": "integer"}},
}
_STRETCH_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["when", "weekday", "month", "day"],
    "properties": {"when": {"type": "string", "enum": list(WHEN)},
                   "weekday": {"type": "string", "enum": list(WEEKDAYS)},
                   "month": {"type": "integer"}, "day": {"type": "integer"}},
}
_CLOCK_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["relation", "hour", "minute", "half", "to_hour", "to_minute", "to_half"],
    "properties": {"relation": {"type": "string", "enum": list(RELATIONS)},
                   "hour": {"type": "integer"}, "minute": {"type": "integer"},
                   "half": {"type": "string", "enum": list(HALVES)},
                   "to_hour": {"type": "integer"}, "to_minute": {"type": "integer"},
                   "to_half": {"type": "string", "enum": list(HALVES)}},
}
SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["when", "weekday", "month", "day", "days_ahead", "through", "also",
                 "break_name", "side", "part_of_day", "clock", "date_kind", "term", "home_away",
                 "about", "other"],
    "properties": {
        "when": {"type": "string", "enum": list(WHEN)},
        "weekday": {"type": "string", "enum": list(WEEKDAYS)},
        "month": {"type": "integer"},
        "day": {"type": "integer"},
        "days_ahead": {"type": "integer"},
        "through": _DAY_SCHEMA,
        "also": {"type": "array", "items": _STRETCH_SCHEMA},
        "break_name": {"type": "string", "enum": list(BREAKS)},
        "side": {"type": "string", "enum": list(SIDES)},
        "part_of_day": {"type": "string", "enum": list(PARTS)},
        "clock": _CLOCK_SCHEMA,
        "date_kind": {"type": "string", "enum": list(DATE_KINDS)},
        "term": {"type": "string", "enum": list(TERMS)},
        "home_away": {"type": "string", "enum": list(HOME_AWAY)},
        "about": {"type": "string"},
        "other": {"type": "array", "items": {"type": "string"}},
    },
}
# The longest "about" or "other" phrase kept: a few words, not the question again.
MAX_ABOUT_CHARS = 80


@dataclass(frozen=True, slots=True)
class Day:
    """The last day of a range: a weekday, or a month and day. Blank: no range."""

    weekday: str = "none"
    month: int = 0
    day: int = 0


@dataclass(frozen=True, slots=True)
class Stretch:
    """One more day or week named beside the first ("mon and tues": tuesday)."""

    when: str = "none"
    weekday: str = "none"
    month: int = 0
    day: int = 0


@dataclass(frozen=True, slots=True)
class Clock:
    """A clock time that limits what is asked, as the student said it: the read copies the
    numbers and never works one out. `half` none: the student did not say am or pm."""

    relation: str = "none"
    hour: int = 0
    minute: int = 0
    half: str = "none"
    to_hour: int = 0
    to_minute: int = 0
    to_half: str = "none"


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
    # What the events asked are about (events.asked_topic): "" when the question asks only what
    # is on; None when the read gave no such pick, and the question's own words are the topic.
    about: str | None = None
    days_ahead: int = 0
    through: Day = Day()
    also: tuple[Stretch, ...] = ()
    clock: Clock = Clock()
    # Other conditions in the student's own words, which no list holds: always said as not
    # checked (conditions.py).
    other: tuple[str, ...] = ()
    # Set by vote(), never by a read: the conditions most reads saw but did not agree on
    # ("days", "clock", "home_away", "other").
    unsure: tuple[str, ...] = field(default=())

    def as_trace(self) -> dict[str, Any]:
        """Only the picks that say something, for the turn's trace."""
        blank = AskedTime()
        said: dict[str, Any] = {}
        for name in (f.name for f in fields(self)):
            value = getattr(self, name)
            if value == getattr(blank, name) or value == "":
                continue
            if isinstance(value, Day | Clock):
                value = _named(value)
            elif name == "also":
                value = [_named(s) for s in value]
            elif isinstance(value, tuple):
                value = list(value)
            said[name] = value
        return said

    @property
    def span(self) -> tuple[Any, ...]:
        """The picks that together say which days: voted on as one."""
        return (self.when, self.weekday, self.month, self.day, self.break_name, self.side,
                self.part_of_day, self.days_ahead, self.through, self.also)


def _named(value: Day | Stretch | Clock) -> dict[str, Any]:
    return {f.name: getattr(value, f.name) for f in fields(value)
            if getattr(value, f.name) not in ("none", 0)}


def _number(value: Any, low: int, high: int) -> int:
    """A whole number in range, else 0 (not named)."""
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return value if low <= value <= high else 0


def _day_of(value: Any) -> Day:
    if not isinstance(value, Mapping):
        return Day()
    weekday = value.get("weekday")
    return Day(weekday if weekday in WEEKDAYS else "none", _number(value.get("month"), 1, 12),
               _number(value.get("day"), 1, 31))


def _also_of(value: Any) -> tuple[Stretch, ...]:
    if not isinstance(value, list | tuple):
        return ()
    found: list[Stretch] = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("when") not in WHEN[1:]:
            continue
        weekday = item.get("weekday")
        stretch = Stretch(str(item["when"]), weekday if weekday in WEEKDAYS else "none",
                          _number(item.get("month"), 1, 12), _number(item.get("day"), 1, 31))
        if stretch not in found:
            found.append(stretch)
    return tuple(found[:MAX_ALSO])


def _clock_of(value: Any) -> Clock:
    """The clock limit, its numbers checked: a 24-hour hour ("17") is 5 pm, and a between with no
    end is "after" its start. No hour: no clock limit."""
    if not isinstance(value, Mapping) or value.get("relation") not in RELATIONS[1:]:
        return Clock()

    def hour_of(hour: Any, half: Any) -> tuple[int, str]:
        hour = _number(hour, 0, 23)
        half = half if half in HALVES else "none"
        if hour > 12:
            return hour - 12, "pm"
        return hour, str(half)
    hour, half = hour_of(value.get("hour"), value.get("half"))
    if not hour and value.get("half") != "am":
        return Clock()
    hour = hour or 12  # "0 am" is midnight.
    relation = str(value["relation"])
    to_hour, to_half = hour_of(value.get("to_hour"), value.get("to_half"))
    if relation != "between":
        to_hour, to_half = 0, "none"
    elif not to_hour:
        relation = "after"
    return Clock(relation, hour, _number(value.get("minute"), 0, 59), half, to_hour,
                 _number(value.get("to_minute"), 0, 59) if to_hour else 0, to_half)


def _other_of(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    said = [" ".join(p.split()) for p in value if isinstance(p, str)]
    kept = [p for p in dict.fromkeys(said) if p and len(p) <= MAX_ABOUT_CHARS]
    return tuple(kept[:MAX_OTHER])


def of(arguments: Any) -> AskedTime | None:
    """The read's time picks, each checked against its list; None when the shape is wrong.
    A month or day out of range counts as not named. The conditions added on 11-19 (range, more
    days, days ahead, clock, other) are each dropped alone when malformed, and read as not named
    when absent, so a read without them is read as before."""
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
    about = arguments.get("about")
    about = " ".join(about.split()) if isinstance(about, str) else None
    if about is not None and len(about) > MAX_ABOUT_CHARS:
        about = None  # The question again, not what it is about: its own words are read.
    when, picked = str(arguments["when"]), {name: str(arguments[name]) for name in lists}
    return AskedTime(when=when, weekday=picked["weekday"], break_name=picked["break_name"],
                     side=picked["side"], part_of_day=picked["part_of_day"],
                     date_kind=picked["date_kind"], term=picked["term"],
                     home_away=picked["home_away"], month=month, day=day, about=about,
                     days_ahead=(_number(arguments.get("days_ahead"), 1, MAX_DAYS_AHEAD)
                                 if when == "coming_days" else 0),
                     through=_day_of(arguments.get("through")),
                     also=_also_of(arguments.get("also")),
                     clock=_clock_of(arguments.get("clock")),
                     other=_other_of(arguments.get("other")))


def vote(picks: Sequence[AskedTime]) -> AskedTime | None:
    """What most reads pick, condition by condition. The days count as one (a read's "friday" and
    another's "evening" never mix into days neither read gave); the clock, home or away, the kind
    of date and the term are each voted alone. A tie goes to "not named", the safe default; but
    when most reads named days, a clock or home or away and no one value has most of them, the
    condition is said to be unsure (11-19 plan: never drop a condition in silence). What the
    events are about counts when most reads give one (most say "" when most ask only what is
    on), in the wording most give, then the shortest; with no majority either way, the
    question's own words are read (None). Another condition's phrase counts when most reads give
    it or a phrase within it."""
    if not picks:
        return None
    half = len(picks) // 2
    unsure: list[str] = []

    def most(values: Sequence[Any], blank: Any, name: str | None = None) -> Any:
        value, count = Counter(values).most_common(1)[0]
        if count > half:
            return value
        if name is not None and sum(v != blank for v in values) > half:
            unsure.append(name)
        return blank

    blank = AskedTime()
    (when, weekday, month, day, break_name, side, part, days_ahead, through,
     also) = most([p.span for p in picks], blank.span, "days")
    clock = most([p.clock for p in picks], blank.clock, "clock")
    home_away = most([p.home_away for p in picks], blank.home_away, "home_away")
    other, other_unsure = _others([p.other for p in picks], half)
    if other_unsure:
        unsure.append("other")
    return AskedTime(when=when, weekday=weekday, month=month, day=day, break_name=break_name,
                     side=side, part_of_day=part,
                     date_kind=most([p.date_kind for p in picks], blank.date_kind),
                     term=most([p.term for p in picks], blank.term), home_away=home_away,
                     about=_about([p.about for p in picks], half), days_ahead=days_ahead,
                     through=through, also=also, clock=clock, other=other,
                     unsure=tuple(unsure))


def _about(given: Sequence[str | None], half: int) -> str | None:
    said = [a for a in given if a is not None]
    if len([a for a in said if not a]) > half:
        return ""
    named = [a.casefold() for a in said if a]
    if len(named) > half:
        return min(named, key=lambda a: (-named.count(a), len(a), a))
    return None


def _others(given: Sequence[tuple[str, ...]], half: int) -> tuple[tuple[str, ...], bool]:
    """The other conditions most reads give, shortest wording first ("commuters" within "for
    commuters"), and whether most reads gave one but none had a majority."""
    said = [[p.casefold() for p in phrases] for phrases in given]
    kept: list[str] = []
    for phrase in sorted({p for phrases in said for p in phrases}, key=lambda p: (len(p), p)):
        backed = sum(any(phrase in p or p in phrase for p in phrases) for phrases in said)
        if backed > half and not any(phrase in k or k in phrase for k in kept):
            kept.append(phrase)
    return tuple(kept[:MAX_OTHER]), not kept and sum(bool(p) for p in said) > half


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
    if when == "coming_days" and asked.days_ahead:
        n = asked.days_ahead
        return now, _end(today + timedelta(days=n), now), \
            f"the next {n} day{'s' if n != 1 else ''}"
    # A time the picks cannot place on their own, beside a named break: the break places it
    # ("next mon and tues before thanksgiving break" picks next_weekday with no one weekday,
    # 11-18 measure, which fell to the two weeks).
    if asked.break_name != "none":
        return _break_window(asked, now, breaks)
    return default_window(now)


_PART_HOURS = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}


def _day(moment: datetime | date) -> str:
    return f"{moment:%A, %B} {moment.day}"


def _span_said(first: datetime, last: datetime) -> str:
    return _day(first) if first.date() == last.date() else f"{_day(first)} to {_day(last)}"


def _joined(said: Sequence[str]) -> str:
    return said[0] if len(said) == 1 else f"{', '.join(said[:-1])} and {said[-1]}"


@dataclass(frozen=True, slots=True)
class Hours:
    """A limit on the time of day, in minutes after midnight (1440: the day's end). "on": the
    item is on at some time from `first` to `last` (a part of the day, after, around). "starts":
    it starts from `first` up to, not at, `last` (before, between)."""

    how: str
    first: int
    last: int


@dataclass(frozen=True, slots=True)
class Window:
    """The campus time a question asks about: from `start` to `end`, named `label`, made of one
    or more stretches (`pieces`, in time order, none touching another), each day limited by
    `hours`. `days_said` says each stretch's dates when there is more than one; `unplaced` names
    the conditions the days could not hold ("through", "also"): the answer says so."""

    start: datetime
    end: datetime
    label: str
    pieces: tuple[tuple[datetime, datetime], ...]
    hours: tuple[Hours, ...] = ()
    days_said: str | None = None
    unplaced: tuple[str, ...] = ()

    def on_days(self, began: datetime, ended: datetime | None) -> bool:
        """Whether an item starts in a stretch asked or is still on when one opens."""
        return any(began <= last and (began >= first or (ended is not None and ended > first))
                   for first, last in self.pieces)

    def on_dates(self, began: datetime) -> bool:
        """Whether an item with no time listed is on a day asked."""
        on = began.astimezone(self.start.tzinfo).date() if self.start.tzinfo else began.date()
        return any(first.date() <= on <= last.date() for first, last in self.pieces)

    def in_hours(self, began: datetime, ended: datetime | None) -> bool:
        """Whether an item fits every limit on the time of day, on its own day."""
        if self.start.tzinfo is not None and began.tzinfo is not None:
            began = began.astimezone(self.start.tzinfo)
        midnight = datetime.combine(began.date(), time(0, 0), tzinfo=began.tzinfo)
        for limit in self.hours:
            first = midnight + timedelta(minutes=limit.first)
            last = midnight + timedelta(minutes=limit.last)
            if limit.how == "starts":
                fits = first <= began < last
            else:
                fits = began <= last and (began >= first or (ended is not None and ended > first))
            if not fits:
                return False
        return True

    def holds(self, began: datetime, ended: datetime | None) -> bool:
        return self.on_days(began, ended) and self.in_hours(began, ended)


def _minutes(hour: int, minute: int, half: str, near: str) -> int:
    """A clock time in minutes after midnight. With no am or pm said, the day's part or "tonight"
    says it (`near`), else 8 to 11 are morning and 12 and 1 to 7 afternoon or evening."""
    if half == "none":
        half = near if near != "none" else ("am" if 8 <= hour <= 11 else "pm")
    return (hour % 12 + (12 if half == "pm" else 0)) * 60 + minute


def minutes_of(text: Any) -> int | None:
    """A listed clock time ("7:00 PM", "5:30pm") in minutes after midnight; None when there is
    none ("TBA", "")."""
    found = re.match(r"\s*(\d{1,2})(?::(\d{2}))?\s*([AP])\.?M", str(text or ""), re.I)
    if not found:
        return None
    hour = int(found.group(1)) % 12 + (12 if found.group(3).upper() == "P" else 0)
    return hour * 60 + int(found.group(2) or 0)


def clock_said(minutes: int) -> str:
    """A clock time as the answer says it: "2 PM", "2:30 PM", "noon", "midnight"."""
    minutes %= 1440
    if minutes == 720:
        return "noon"
    if minutes == 0:
        return "midnight"
    hour, minute = divmod(minutes, 60)
    shown = f"{hour % 12 or 12}" + (f":{minute:02d}" if minute else "")
    return f"{shown} {'PM' if hour >= 12 else 'AM'}"


def _clock_hours(asked: AskedTime) -> tuple[Hours, str] | None:
    """The clock limit as a limit on the time of day, and as the answer says it ("before 2 PM").
    "Around" is the hour either side of the time."""
    clock = asked.clock
    if clock.relation == "none":
        return None
    near = ("pm" if asked.when == "tonight" or asked.part_of_day in ("afternoon", "evening")
            else "am" if asked.part_of_day == "morning" else "none")
    at = _minutes(clock.hour, clock.minute, clock.half, near)
    if clock.relation == "before":
        return Hours("starts", 0, at), f"before {clock_said(at)}"
    if clock.relation == "after":
        return Hours("on", at, 1440), f"after {clock_said(at)}"
    if clock.relation == "around":
        return Hours("on", max(at - 60, 0), min(at + 60, 1440)), f"around {clock_said(at)}"
    until = _minutes(clock.to_hour, clock.to_minute,
                     clock.to_half if clock.to_half != "none" else
                     (clock.half if clock.half != "none" else near), "none")
    if until <= at and until + 720 < 1440:
        until += 720  # "between 7 and 9": 7 PM to 9 PM.
    if until <= at:
        return Hours("starts", at, 1440), f"after {clock_said(at)}"
    return Hours("starts", at, until), f"between {clock_said(at)} and {clock_said(until)}"


def _through(last: Day, first: date) -> date | None:
    """The last day of a range starting on `first`: a month and day (a day alone is in the
    first day's month), or the first such weekday after it. None when it cannot be placed."""
    if last.day:
        month = last.month or first.month
        for year in (first.year, first.year + 1):
            try:
                day = date(year, month, last.day)
            except ValueError:
                return None
            if day >= first:
                return day if (day - first).days <= MAX_RANGE_DAYS else None
        return None
    if last.weekday != "none":
        ahead = (WEEKDAYS.index(last.weekday) - 1 - first.weekday()) % 7 or 7
        return first + timedelta(days=ahead)
    return None


def asked_window(asked: AskedTime | None, now: datetime,
                 breaks: Sequence[tuple[str, date, date]] = ()) -> Window:
    """The campus time the picks ask about; the next DEFAULT_DAYS days when they name no time,
    or there was no read. A range runs from the day picked to its last day; more days are
    stretches of their own, joined where they touch; a part of the day and a clock limit hold on
    every day. `breaks` are the academic calendar's (name, first day, last day)."""
    if asked is None:
        start, end, label = default_window(now)
        return Window(start, end, label, ((start, end),))
    start, end, label = _days(asked, now, breaks)
    unplaced: list[str] = []
    if asked.through != Day():
        last = _through(asked.through, start.date())
        if last is None or label == DEFAULT_LABEL:
            unplaced.append("through")
        else:
            end = max(end, _end(last, now))
            label = _span_said(start, end)
    stretches = [] if label == DEFAULT_LABEL and asked.also else [(start, end, label)]
    for more in asked.also:
        found = _days(AskedTime(when=more.when, weekday=more.weekday, month=more.month,
                                day=more.day), now, breaks)
        if found[2] == DEFAULT_LABEL:
            unplaced.append("also")
        else:
            stretches.append(found)
    if not stretches:
        stretches = [(start, end, label)]
    stretches.sort(key=lambda s: s[0])
    pieces: list[tuple[datetime, datetime]] = []
    for first, last, _ in stretches:
        if pieces and first <= pieces[-1][1] + timedelta(seconds=1):
            pieces[-1] = (pieces[-1][0], max(pieces[-1][1], last))
        else:
            pieces.append((first, last))
    labels = list(dict.fromkeys(s[2] for s in stretches))
    label = _joined(labels)
    hours: list[Hours] = []
    part = asked.part_of_day
    if part != "none" and labels != ["tonight"]:
        first_hour, last_hour = _PART_HOURS[part]
        hours.append(Hours("on", first_hour * 60, last_hour * 60))
        if len(pieces) == 1 and pieces[0][0].date() == pieces[0][1].date():
            # One day: the window itself is that part of the day.
            day = pieces[0][0].date()
            first = max(pieces[0][0], datetime.combine(day, time(first_hour, 0),
                                                       tzinfo=now.tzinfo))
            last = (datetime.combine(day, time(last_hour, 0), tzinfo=now.tzinfo)
                    if last_hour < 24 else pieces[0][1])
            pieces[0] = (first, last)
        label = f"{label}, {part}"
    if (clocked := _clock_hours(asked)) is not None:
        hours.append(clocked[0])
        label = f"{label}, {clocked[1]}"
    return Window(pieces[0][0], pieces[-1][1], label, tuple(pieces), tuple(hours),
                  _joined([_span_said(a, b) for a, b in pieces]) if len(pieces) > 1 else None,
                  tuple(unplaced))


def window(asked: AskedTime | None, now: datetime,
           breaks: Sequence[tuple[str, date, date]] = ()) -> tuple[datetime, datetime, str]:
    """The span of campus time the picks ask about, from its first day to its last, and its
    name (asked_window)."""
    found = asked_window(asked, now, breaks)
    return found.start, found.end, found.label


def phrase(label: str, start: datetime, end: datetime, days_said: str | None = None) -> str:
    """A window as a reply says it: "this weekend, Friday, November 6 to Sunday, November 8",
    "tonight, Monday, November 2", "on Friday, November 6, in the evening", "today, Wednesday,
    November 18, before 2 PM", "on Monday, November 23 and Wednesday, November 25" (`days_said`:
    each stretch's dates, when there is more than one)."""
    base, clock, part = label, "", ""
    head, _, tail = base.rpartition(", ")
    if head and tail.split(" ")[0] in RELATIONS[1:] \
            and tail.endswith((" AM", " PM", "noon", "midnight")):
        base, clock = head, f", {tail}"
    for name in PARTS[1:]:
        if base.endswith(f", {name}"):
            base, part = base[: -len(name) - 2], f", in the {name}"
    span = days_said or _span_said(start, end)
    if base.split(",")[0].casefold() in WEEKDAYS[1:]:
        on = "on" if days_said or start.date() == end.date() else "from"
        return f"{on} {span}{part}{clock}"
    lead = {"the next": "in ", "the rest": "for "}.get(" ".join(base.split()[:2]), "")
    return f"{lead}{base}, {span}{part}{clock}"


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
    months = [asked.month] if asked.when in ("month", "date") else []
    months += [asked.through.month] + [s.month for s in asked.also if s.when in ("month", "date")]
    words += [_MONTH_WORDS[m - 1] for m in months if 1 <= m <= 12]
    if asked.when == "break" or asked.break_name != "none" or asked.date_kind == "break":
        words += [asked.break_name, _BREAK_WORDS]
    if asked.when == "rest_of_term" or asked.date_kind == "finals":
        words.append(_TERM_WORDS)
    return frozenset(" ".join(words).split()) - {"none"}


# The current turn's picks, set once the read is in (engine._read_then_answer) and read where a
# time is turned into days, so each answer path need not pass them along. None: no read, and the
# question gets the usual window.
ASKED: ContextVar[AskedTime | None] = ContextVar("asked_time", default=None)
