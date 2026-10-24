"""Campus events and clubs: which calendar events a question asks about, and which clubs.

Code reads the question's time words into a window on the campus clock ("tonight", "this
weekend", "next friday", "oct 30"; the next two weeks when none), keeps the calendar events whose
date (in their identity's name) falls in it, and ranks them by the question's other words against
each event's title, tags, organizer and location. A club is named by its directory name. The
events and clubs are then read through the shared reader; the model only words the reply.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any

from rockygpt_brain.retrieval.entity_facts import discovery_tokens

CALENDAR_URL = "https://archway.ramapo.edu/home/events/"
MAX_EVENTS = 15
DEFAULT_DAYS = 14
_EVENT_WORDS = re.compile(
    r"\b(?:events?|happening|going on|games?|match(?:es)?|"
    r"shows?|concerts?|performances?|plays?|recitals?|fairs?|workshops?|speakers?|lectures?|"
    r"free food|parties|party|things to do|anything to do|calendar|homecoming|tournaments?|"
    r"screenings?|exhibits?|exhibitions?)\b")
# A time alone asks for the calendar ("anything tonight?"), but not beside a subject code answers
# from its own records: "dinner at the Birch tonight" is the menu, "the last shuttle tonight" the
# timetable, "is the Library open tonight" its hours.
_WHEN_WORDS = re.compile(r"\b(?:tonight|this weekend|next weekend)\b")
_OTHER_SUBJECT = re.compile(
    r"\b(?:menus?|eat|food|meals?|breakfast|brunch|lunch|dinner|dining|shuttles?|bus|buses|"
    r"train|hours|open|opens|close|closes|closing|closed)\b")
_CLUB_WORDS = re.compile(
    r"\b(?:clubs?|organi[sz]ations?|orgs?|greek|sororit(?:y|ies)|fraternit(?:y|ies)|"
    r"student groups?|honor societ(?:y|ies))\b")
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH_DAY = re.compile(r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b")
_NUMERIC_DAY = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b")
_EVENT_DATE = re.compile(r"\((\d{4})-(\d{2})-(\d{2})\)\s*$")
# Words of a question that say when or what kind of thing, not what the event is about.
_NOT_TOPIC = discovery_tokens(
    "what whats is are there any events event happening going on tonight today tomorrow this "
    "next weekend week month on campus at ramapo college upcoming coming up soon when where "
    "who can i we go to do things anything fun free for me my friends students student "
    "monday tuesday wednesday thursday friday saturday sunday morning afternoon evening night "
    "time times date dates schedule calendar")


def asks_events(message: str) -> bool:
    text = message.casefold()
    return bool(_EVENT_WORDS.search(text)
                or (_WHEN_WORDS.search(text) and not _OTHER_SUBJECT.search(text)))


def asks_clubs(message: str) -> bool:
    return bool(_CLUB_WORDS.search(message.casefold()))


def _end(day: date, now: datetime) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=now.tzinfo)


def _start(day: date, now: datetime) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=now.tzinfo)


_PART_OF_DAY = ((re.compile(r"\b(?:night|evening)\b"), 17, 24, "evening"),
                (re.compile(r"\bafternoon\b"), 12, 17, "afternoon"),
                (re.compile(r"\bmorning\b"), 0, 12, "morning"))


def window(message: str, now: datetime) -> tuple[datetime, datetime, str]:
    """The span of campus time a question asks about, and its name; the next two weeks when the
    question names no time. A part of the day ("friday night") narrows a one-day window."""
    start, end, label = _days(message, now)
    if start.date() == end.date() and label != "tonight":
        for words, first, last, name in _PART_OF_DAY:
            if words.search(message.casefold()):
                day = start.date()
                start = max(start, datetime.combine(day, time(first, 0), tzinfo=now.tzinfo))
                end = (datetime.combine(day, time(last, 0), tzinfo=now.tzinfo)
                       if last < 24 else end)
                return start, end, f"{label}, {name}"
    return start, end, label


def _days(message: str, now: datetime) -> tuple[datetime, datetime, str]:
    text = message.casefold()
    today = now.date()
    if "tonight" in text:
        return max(now, datetime.combine(today, time(17, 0), tzinfo=now.tzinfo)), \
            _end(today, now), "tonight"
    if "tomorrow" in text:
        day = today + timedelta(days=1)
        return _start(day, now), _end(day, now), "tomorrow"
    if "today" in text:
        return _start(today, now), _end(today, now), "today"
    weekend = re.search(r"\b(this|next)?\s*weekend\b", text)
    if weekend:
        friday = today + timedelta(days=(4 - today.weekday()) % 7)
        if today.weekday() >= 5:  # Saturday or Sunday: this weekend is now.
            friday = today - timedelta(days=today.weekday() - 4)
        if weekend.group(1) == "next":
            friday += timedelta(days=7)
        start = max(now, datetime.combine(friday, time(17, 0), tzinfo=now.tzinfo))
        return start, _end(friday + timedelta(days=2), now), (
            "next weekend" if weekend.group(1) == "next" else "this weekend")
    if "next week" in text:
        monday = today + timedelta(days=7 - today.weekday())
        return _start(monday, now), _end(monday + timedelta(days=6), now), "next week"
    if "this week" in text:
        return now, _end(today + timedelta(days=6 - today.weekday()), now), "this week"
    for n, name in enumerate(_DAYS):
        if re.search(rf"\b{name}s?\b", text):
            ahead = (n - today.weekday()) % 7
            if re.search(rf"\bnext {name}", text) and ahead == 0:
                ahead = 7
            day = today + timedelta(days=ahead)
            return _start(day, now), _end(day, now), f"{name.title()}, {day:%B} {day.day}"
    found = _MONTH_DAY.search(text)
    numeric = _NUMERIC_DAY.search(text)
    if found or numeric:
        month = (_MONTHS.index(found.group(1)) + 1) if found else int(numeric.group(1))  # type: ignore[union-attr]
        dom = int(found.group(2)) if found else int(numeric.group(2))  # type: ignore[union-attr]
        try:
            day = date(today.year, month, dom)
            if day < today:
                day = date(today.year + 1, month, dom)
            return _start(day, now), _end(day, now), f"{day:%A, %B} {day.day}"
        except ValueError:
            pass
    if "this month" in text:
        last = (date(today.year + (today.month == 12), today.month % 12 + 1, 1)
                - timedelta(days=1))
        return now, _end(last, now), "this month"
    return now, _end(today + timedelta(days=DEFAULT_DAYS), now), f"the next {DEFAULT_DAYS} days"


def events_in(events: Sequence[dict[str, Any]], start: datetime,
              end: datetime) -> list[dict[str, Any]]:
    """The calendar event identities dated within the window (their names end in the date)."""
    found = []
    for event in events:
        dated = _EVENT_DATE.search(str(event["name"]))
        if dated and start.date() <= date(*map(int, dated.groups())) <= end.date():
            found.append(event)
    return found


def topic_words(message: str) -> set[str]:
    """The words of a question that say what an event or club is about."""
    return set(discovery_tokens(message)) - _NOT_TOPIC


# An event found by its card's meaning (title, tags, host, place, admission, description). On
# 13 sample questions (10-22) a topic's best event scored 0.47-0.58 ("free food", "volleyball
# games", "halloween", "arts performances"), questions about no event at most 0.43; events
# within EVENT_NEAR of the window's best count as matching.
EVENT_FLOOR, EVENT_NEAR = 0.44, 0.06


def rank_events(values: Sequence[dict[str, Any]], message: str, start: datetime,
                end: datetime, scores: Mapping[str, float] | None = None,
                ) -> tuple[list[dict[str, Any]], bool | None]:
    """The events in the window that have not ended, in time order, at most MAX_EVENTS: those
    sharing the question's topic words with their title, tags, organizer, location or
    description, and those whose card's meaning (scores, by each value's "_id") is near the
    window's best above EVENT_FLOOR, when any are. Also whether the topic matched: None with no
    topic words, False when none matched (the whole window is then given, and the reply says
    the calendar lists nothing for the topic)."""
    def starts(value: dict[str, Any]) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value.get("starts_at")))
        except ValueError:
            return None
    timed = [v for v in values if (s := starts(v)) is not None
             and start - timedelta(hours=3) <= s <= end]
    timed.sort(key=lambda v: starts(v) or start)
    words = topic_words(message)
    if words:
        def about(value: dict[str, Any]) -> set[str]:
            text = " ".join([str(value.get("title") or ""), " ".join(value.get("tags") or []),
                             str(value.get("organizer") or ""), str(value.get("location") or ""),
                             str(value.get("description") or "")])
            return set(discovery_tokens(text))
        meaning = {id(v): (scores or {}).get(str(v.get("_id")), 0.0) for v in timed}
        best = max(meaning.values(), default=0.0)
        matching = [v for v in timed if words & about(v)
                    or meaning[id(v)] >= max(EVENT_FLOOR, best - EVENT_NEAR)]
        if matching:
            return matching[:MAX_EVENTS], True
        return timed[:MAX_EVENTS], False
    return timed[:MAX_EVENTS], None


def event_line(value: dict[str, Any]) -> dict[str, Any]:
    """What the reply is given for one event, as published."""
    when = " ".join(part for part in (str(value.get("date") or ""),
                                      str(value.get("start_time") or ""),
                                      f"to {value['end_time']}" if value.get("end_time") else "")
                    if part)
    return {"event": value.get("title"), "when": when, "location": value.get("location"),
            "organizer": value.get("organizer"), "tags": value.get("tags") or [],
            **({"tickets": value["ticket_status"]} if value.get("ticket_status") else {}),
            "url": value.get("url")}


_GENERIC_CLUB = discovery_tokens("club clubs society association organization union team group "
                                 "student students council chapter")


def find_clubs(clubs: Sequence[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """The clubs a message names by every word of their directory name (longest first); a name
    of only generic words ("Club") names nothing."""
    words = discovery_tokens(message)
    named = [(club, core) for club in clubs
             if (core := discovery_tokens(str(club["name"]))) and core <= words
             and not core <= _NOT_TOPIC | _GENERIC_CLUB]
    named.sort(key=lambda pair: -len(pair[1]))
    return [club for club, _ in named][:6]


# A club found by its card's meaning (name and category). On 10 sample questions (10-22) club
# questions scored 0.45-0.67 against their best club and others at most 0.31.
CLUB_FLOOR, CLUB_NEAR, MAX_CLUBS = 0.44, 0.08, 8


def clubs_by_meaning(scores: Sequence[tuple[str, float]],
                     names: dict[str, str]) -> list[dict[str, Any]]:
    """The clubs whose cards best match a question: above the floor and near the best, at most
    MAX_CLUBS ("cultural clubs" lists several, "is there an anime club" the nearest few)."""
    if not scores or scores[0][1] < CLUB_FLOOR:
        return []
    return [{"id": entity_id, "name": names[entity_id]} for entity_id, score in scores
            if score >= max(CLUB_FLOOR, scores[0][1] - CLUB_NEAR) and entity_id in names
            ][:MAX_CLUBS]


MAX_CATEGORY_CLUBS = 25
_GENERIC_CATEGORY = {"student organization", "department", "office sponsored organization"}


def club_category(found: Sequence[dict[str, Any]],
                  directory: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every club of the category most of the clubs found share, when it is a specific one
    ("Greek Life", "Honor Society": not "Student Organization"): a question about a kind of
    club wants the kind, not the few nearest. Empty when no such category leads."""
    def head(entry: dict[str, Any]) -> str:
        return str(entry.get("category") or "").split(" - ")[0].strip().casefold()
    heads = [head(e) for e in found if head(e)]
    if len(heads) < 3:
        return []
    top = max(set(heads), key=heads.count)
    if top in _GENERIC_CATEGORY or heads.count(top) * 2 < len(found):
        return []
    return [e for e in directory if head(e) == top]


def interests(message: str) -> list[str]:
    """The interests a club question lists, each on its own ("anime and community service"):
    the question's clauses split at "and", "or" and commas, keeping those with a topic word."""
    text = re.sub(r"\b(?:which|what|club|clubs|should|could|would|join|i|like|love|into|"
                  r"interested in|am|im|i'm|do|you|have|are|there|any)\b", " ", message.casefold())
    bits = [b.strip(" ?.!") for b in re.split(r",|\band\b|\bor\b|&|/", text)]
    return [b for b in bits if topic_words(b)][:4]

