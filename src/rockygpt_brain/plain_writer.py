"""Plain sentences for a lookup answer, written by code from the writer view.

Dan 11-02: a lookup answer (hours, contacts, rooms, menus) is said in code's own plain sentences,
the same every time and never with a word the facts do not hold. An AI writer can come back later
as a layer (BRAIN_LOOKUP_WRITER=1). `plain_text` says what the writer view holds by the AI writer's
own rules (writer.py WRITER_PROMPT) for the shapes it knows, and returns None for any shape it does
not, so the code's full evidence text is the answer instead of a guess.
"""

import re
from datetime import date, datetime
from typing import Any

from rockygpt_brain.answers import citation_url, us_phone
from rockygpt_brain.writer_view import DATES_NOT_PUBLISHED_NOTE


class CannotSay(Exception):
    """A shape this writer does not know how to say."""


_VIEW_KEYS = frozenset({"asked", "status", "facts", "derived_facts", "not_published", "missing",
                        "notices", "sources"})
_DERIVED_KEYS = frozenset({"subject", "predicate", "day", "date", "value", "sources", "open_now",
                           "listed_open_now", "open_at", "listed_open_at", "applies",
                           "applicability", "applicability_reason", "current"})
_DAY_KEYS = frozenset({"schedule", "hours", "periods", "notes", "season", "status", "absence",
                       "dates_not_published", "listed_hours", "closed_gaps", "meal_asked",
                       "window", "meals_not_labelled", "meal_not_served"})
_OPEN_KEYS = frozenset({"state", "now", "closes", "meal", "meal_ends", "then_meal", "reopens",
                        "reopens_meal", "last_closes", "next_meal", "next_opens"})
# What the hours say for a time the student asked about (fact_packet.open_at).
_OPEN_AT_KEYS = frozenset({"relation", "at", "state", "periods", "last_closes", "closes", "meal",
                           "next_opens", "next_meal", "all_day"})
_PERIOD_KEYS = frozenset({"label", "opens", "closes", "open_at_asked"})
_WEEK_KEYS = frozenset({"schedule", "days", "notes", "season", "dates_not_published"})
_MENU_KEYS = frozenset({"date", "meals", "meal_asked", "filter", "matched", "on_menu",
                        "matches_cut", "stations_listed", "details_asked", "rest_of_menu",
                        "rest_of_menu_cut", "whole_menu_asked", "meal_not_listed",
                        "meals_listed"})
_ABSENCE_KEYS = frozenset({"subject", "predicate", "checked_at", "pages", "sources", "current",
                           "scope", "reason", "purpose"})
# What a confirmed absence says is not published, and what missing evidence leaves unknown.
_NOT_PUBLISHED = {"email": "a general email address", "phones": "a general phone number",
                  "offices": "a room", "hours": "its hours", "website": "a website",
                  "contact_note": "contact instructions", "department": "a department",
                  "prefers_email": "whether it prefers email",
                  "preferred_contact": "a preferred way to reach it"}
_UNKNOWN = {"email": "email address", "phones": "phone number", "offices": "location",
            "hours": "hours", "website": "website", "contact_note": "contact instructions",
            "department": "department", "menu": "menu", "name": "name"}
# A menu names at most this many items of each station, then "and N more" (Dan 11-02: a dinner
# answer was about 70 items, toppings and repeats included).
MENU_ITEMS = 4
# An office's phone lines said, its own first as published, then "and N more on its page" (the
# Registrar lists eight, six of them staff members').
PHONE_LINES = 4
# A building's places beside where it is are named up to this many, then "and N more" (the
# Student Center has 15). Asked what is in it, every one is named.
PLACES_SAID = 8
# A page section is said in its own words, up to the end of the sentence that passes this many
# characters, and the rest is left on its page.
SECTION_CHARS = 500
# How long the page sections a reply quotes may be, each step shorter (a subject's first section,
# then its others; 0 names the page only), when the reply would be longer than a reply can be
# (11-03 practice: a suitemate question's six sections came to 4,400 characters, past the writer's
# limit, and the reply fell back to the code's evidence text with its internal review notes).
QUOTE_STEPS = ((SECTION_CHARS, SECTION_CHARS), (250, 250), (250, 0), (0, 0))
# A building's own facts (11-03): where the campus map shows it, its room numbers, its places.
_BUILDING_KEYS = ("map", "room_prefixes", "places_in")
# The menu marks no item a topping or a condiment, so an item under this many calories (onions,
# lettuce, pickle chips, ginger) is left out, unless the student named that food.
TOPPING_CALORIES = 50
# A location published as a phrase ("Near the campus South Gate entrance") is said as one.
_PLACE_WORDS = frozenset({"near", "at", "in", "on", "behind", "next", "across", "inside",
                          "outside", "off", "beside", "between", "above", "below"})
# A room given as a floor ("First floor", "2nd Floor"): it is on that floor, not in it.
_FLOOR = re.compile(r"(?:first|second|third|fourth|fifth|ground|main|lower|upper|top|"
                    r"\d+(?:st|nd|rd|th))\s+(?:floor|level)\b", re.IGNORECASE)


def literal(value: Any) -> str:
    """Published text as text: no link, emphasis, heading or HTML can come from a page, while
    parentheses and "&" read as written (answers.literal escapes those too, for its evidence
    text)."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]#|])", r"\\\1", text)


_COUNTS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
           "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()


def _count(n: int) -> str:
    """A count code made ("and four more") in words: it is no number of the facts, and the
    reply's check holds every digit to them. Past twenty the check sends code's evidence text."""
    return _COUNTS[n] if 0 <= n < len(_COUNTS) else str(n)


def _when(value: str, *, year: bool = False) -> str:
    """A date or timestamp as a student reads it: October 23, or October 23, 2026."""
    try:
        day = (datetime.fromisoformat(value).date() if "T" in value
               else date.fromisoformat(value[:10]))
    except ValueError as error:
        raise CannotSay from error
    return f"{day:%B} {day.day}" + (f", {day.year}" if year else "")


def _day(entry: dict[str, Any]) -> str:
    return f"{entry['day']}, {_when(entry['date'])}"


def _marks(sources: Any) -> str:
    if not isinstance(sources, list) or not all(isinstance(n, int) for n in sources):
        raise CannotSay
    return "".join(f"[{n}]" for n in sources)


def _known(item: dict[str, Any], keys: frozenset[str]) -> None:
    if not isinstance(item, dict) or set(item) - keys:
        raise CannotSay


def _plain(name: Any) -> str:
    return re.sub(r"\s*\([A-Z]{2,6}\)\s*$", "", str(name)).casefold().strip()


def _numbers(text: str) -> set[str]:
    return {str(int(n)) for n in re.findall(r"\d+", text)}


def _joined(sentences: list[str]) -> str:
    """Sentences one after another; one of several lines (a menu) starts on a line of its own."""
    text = ""
    for sentence in sentences:
        # A page section quoted is a paragraph of its own.
        apart = "\n" in sentence or _QUOTED_SECTION.search(sentence)
        text += ("" if not text else "\n\n" if apart else " ") + sentence
    return text


_QUOTED_SECTION = re.compile(r"^.{1,120}?'s page (?:\"[^\"]*\" )?says: \"")


class _Reply:
    """Sentences by subject in the order first said, each with its source marks merged."""

    def __init__(self, view: dict[str, Any] | None = None,
                 quotes: tuple[int, int] = QUOTE_STEPS[0]) -> None:
        self.subjects: dict[str, dict[str, list[int]]] = {}
        # How much of a subject's first page section is quoted, and of its others.
        self.quotes = quotes
        self.quoted: set[str] = set()
        self.days_said: set[tuple[str, str]] = set()
        self.dates_note: str | None = None
        self.dates_sources: list[int] = []
        # A page section two subjects share (Public Safety's emergency and non-emergency lines
        # link the same pages) is said once.
        self.sections_said: set[tuple[str, str]] = set()
        # Weekdays a week was said with as "not listed", by subject and day, with the sources
        # it was said from: the gap that says so again is not said twice.
        self.not_listed: dict[tuple[str, str], set[int]] = {}
        view = view or {}
        # Each source's published dates (a dining venue's special hours for November 6 to 7).
        self.windows: dict[int, tuple[str, str]] = {
            source["n"]: (source["valid_from"], source["valid_until"])
            for source in view.get("sources") or []
            if isinstance(source, dict) and isinstance(source.get("n"), int)
            and isinstance(source.get("valid_from"), str)
            and isinstance(source.get("valid_until"), str)}
        # When each source was read: a page section that may have changed since is said with it.
        self.read_on: dict[int, str] = {
            source["n"]: source["captured_at"] for source in view.get("sources") or []
            if isinstance(source, dict) and isinstance(source.get("n"), int)
            and isinstance(source.get("captured_at"), str)}
        asked = view.get("asked") if isinstance(view.get("asked"), dict) else {}
        as_of = asked.get("asOf") if isinstance(asked, dict) else None
        self.year = as_of[:4] if isinstance(as_of, str) and as_of[:4].isdigit() else None
        fields = asked.get("fields") if isinstance(asked, dict) else None
        self.fields = {str(f) for f in fields} if isinstance(fields, list) else set()
        # The rooms asked of a building by its room-number prefix ("Where is ASB 407?").
        rooms = asked.get("rooms") if isinstance(asked, dict) else None
        self.rooms = [r for r in rooms if isinstance(r, str)] if isinstance(rooms, list) else []
        # Whose hours the view holds: a building's hours are said by its places' own.
        self.with_hours = {str(entry.get("subject")) for kind in ("derived_facts", "facts")
                           for entry in view.get(kind) or [] if isinstance(entry, dict)
                           and entry.get("predicate") in ("hours", "hours_on")}
        # The buildings the view says (an office of the same name is the building's office).
        self.buildings = {literal(entry.get("subject")) for entry in view.get("facts") or []
                          if isinstance(entry, dict) and entry.get("predicate") in _BUILDING_KEYS}

    def say(self, subject: str, sentence: str, sources: list[int]) -> None:
        said = self.subjects.setdefault(subject, {})
        marks = said.setdefault(sentence, [])
        marks.extend(n for n in sources if n not in marks)

    def text(self) -> str:
        paragraphs = []
        for said in self.subjects.values():
            marks = {tuple(m) for m in said.values()}
            if len(marks) == 1:
                paragraphs.append(f"{_joined(list(said))} {_marks(list(marks.pop()))}".strip())
            elif said:
                paragraphs.append(_joined([f"{sentence} {_marks(m)}".strip()
                                           for sentence, m in said.items()]))
        if self.dates_note:
            paragraphs.append(f"{self.dates_note} {_marks(self.dates_sources)}".strip())
        return "\n\n".join(paragraphs)


def _and(items: list[str]) -> str:
    """Items as a sentence lists them: "Friday", "Friday and Saturday", "Monday, Friday and
    Sunday"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


_WEEK = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _days(days: list[Any]) -> str:
    if not days or not all(isinstance(d, str) for d in days):
        raise CannotSay
    return "any day" if set(days) >= set(_WEEK) else _and([literal(d) for d in days])


def _schedule_name(value: dict[str, Any], subject: str) -> str:
    """A schedule as code's text names it: its own name, with its season when the name lacks it
    ("Student Accounts (Academic Year)")."""
    schedule, season = value.get("schedule"), value.get("season")
    named = literal(schedule) if schedule else subject
    if season and str(season) not in str(schedule or ""):
        named += f" ({literal(season)})"
    return named


def _window(reply: _Reply, sources: list[int]) -> str | None:
    """The dates a schedule's sources publish ("November 6 to November 7"), or None when one of
    them publishes none. The year is said when it is not the year asked in."""
    if not sources or any(n not in reply.windows for n in sources):
        return None
    said = []
    for start, end in dict.fromkeys(reply.windows[n] for n in sources):
        year = reply.year is None or start[:4] != reply.year or end[:4] != reply.year
        first, last = _when(start, year=year), _when(end, year=year)
        said.append(first if first == last else f"{first} to {last}")
    return _and(said)


def _dates(reply: _Reply, value: dict[str, Any], fact: dict[str, Any],
           sources: list[int]) -> str | None:
    """For a schedule that is not current, the dates its sources publish (Dunkin's special hours
    "November 6 to November 7"). One whose dates are not published (a season with no dates) adds
    the note that says so and has none. One that is not current for no reason the view gives
    can't be said."""
    if not (value.get("dates_not_published") or fact.get("current") is False):
        return None
    if value.get("dates_not_published") or value.get("season"):
        _undated(reply, {}, sources)
        return None
    window = _window(reply, sources)
    if window is None:
        raise CannotSay
    return window


def _undated(reply: _Reply, entry: dict[str, Any], sources: list[int]) -> None:
    reason = entry.get("applicability_reason")
    reply.dates_note = reply.dates_note or (literal(reason) if isinstance(reason, str)
                                            else DATES_NOT_PUBLISHED_NOTE)
    reply.dates_sources.extend(n for n in sources if n not in reply.dates_sources)


def _notes(reply: _Reply, subject: str, notes: Any, said: str, sources: list[int]) -> None:
    """A schedule's notes, in the page's own words, except one that only repeats its hours."""
    if notes is None:
        return
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        raise CannotSay
    for note in notes:
        if not _numbers(note) <= _numbers(said) or not _numbers(note):
            reply.say(subject, f'The page adds: "{literal(note.strip())}"', sources)


def _open_now(subject: str, now: dict[str, Any], listed: bool) -> list[str]:
    _known(now, _OPEN_KEYS)
    state = now.get("state")
    if state not in ("open", "closed") or not isinstance(now.get("now"), str):
        raise CannotSay
    by = "By its listed hours, " if listed else ""
    said = [f"{by}{subject} is {state} now ({now['now']})."]
    if state == "open":
        if now.get("meal"):
            serving = f"It is serving {literal(now['meal'])}"
            if now.get("meal_ends") and now.get("then_meal"):
                serving += f" until {now['meal_ends']}, then {literal(now['then_meal'])}"
            elif now.get("closes"):
                serving += f" until {now['closes']}"
            said.append(serving + ".")
        if now.get("closes") and (now.get("then_meal") or not now.get("meal")):
            closes = f"It closes at {now['closes']}"
            if now.get("reopens"):
                closes += f" and reopens at {now['reopens']}"
                if now.get("reopens_meal"):
                    closes += f" for {literal(now['reopens_meal'])}"
                if now.get("last_closes"):
                    closes += f" until {now['last_closes']}"
            said.append(closes + ".")
    elif now.get("next_opens"):
        said.append(f"It opens next at {now['next_opens']}"
                    + (f" for {literal(now['next_meal'])}" if now.get("next_meal") else "") + ".")
    return said


def _open_at(subject: str, day: str, at: Any, listed: bool) -> str:
    """The time the student asked about, said for one place: what it is open for from that time
    on ("after 9pm"), or whether it is open at that time, as code worked it out."""
    if not isinstance(at, dict):
        raise CannotSay
    _known(at, _OPEN_AT_KEYS)
    relation, time, state = at.get("relation"), at.get("at"), at.get("state")
    if relation not in ("after", "at") or state not in ("open", "closed") \
            or not isinstance(time, str):
        raise CannotSay
    by = "By its listed hours, " if listed else ""
    if at.get("all_day") is True and state == "open":
        return f"{by}{subject} is open 24 hours on {day}."
    if relation == "after" and state == "closed":
        if isinstance(at.get("last_closes"), str):
            return f"{by}{subject} is not open after {time} on {day}: it closes at " \
                   f"{at['last_closes']}."
        return f"{by}{subject} is closed on {day}."
    if relation == "after":
        periods = at.get("periods")
        if not isinstance(periods, list) or not periods:
            raise CannotSay
        for period in periods:
            _known(period, _PERIOD_KEYS)
        labelled = all(isinstance(p.get("label"), str) for p in periods)
        spans = [(f"{literal(p['label'])} " if labelled else "")
                 + (f"until {p['closes']}" if p.get("open_at_asked")
                    else f"from {p['opens']} to {p['closes']}") for p in periods]
        lead = f"after {time} on {day}" if listed else f"After {time} on {day}"
        return f"{by}{lead}, {subject} {'serves' if labelled else 'is open'} {_and(spans)}."
    if state == "open":
        serving = f"serving {literal(at['meal'])}" if at.get("meal") else ""
        until = f"until {at['closes']}" if at.get("closes") else ""
        tail = " ".join(part for part in (serving, until) if part)
        return f"{by}{subject} is open at {time} on {day}" + (f", {tail}." if tail else ".")
    said = f"{by}{subject} is closed at {time} on {day}."
    if at.get("next_opens"):
        said += f" It opens at {at['next_opens']}" + (
            f" for {literal(at['next_meal'])}" if at.get("next_meal") else "") + "."
    return said


def _day_hours(reply: _Reply, entry: dict[str, Any], brief: bool = False) -> None:
    """One office's or venue's hours on the day asked (a derived hours_on fact). Beside its menu
    (`brief`) only when the meal asked is served, not whether the venue is open now."""
    _known(entry, _DERIVED_KEYS)
    if entry.get("applies") is False:
        return  # A schedule for other dates: it is not that day's.
    value, sources = entry["value"], entry["sources"]
    _known(value, _DAY_KEYS)
    subject, day = literal(entry["subject"]), _day(entry)
    reply.days_said.add((subject, entry["day"]))
    schedule = value.get("schedule")
    named = (literal(schedule) if schedule and _plain(schedule) != _plain(entry["subject"])
             else subject)
    asked_time = entry.get("open_at") or entry.get("listed_open_at")
    now_said = False
    if asked_time is not None and not brief:
        # The time asked is said first, after whether it is open now when that was asked too.
        for key, listed_now in (("open_now", False), ("listed_open_now", True)):
            if key in entry:
                for sentence in _open_now(subject, entry[key], listed_now):
                    reply.say(subject, sentence, sources)
                now_said = True
        listed = "listed_open_at" in entry
        said = _open_at(subject, day, asked_time, listed)
        reply.say(subject, said, sources)
        if asked_time["relation"] == "after":
            # "Food after 9pm" is answered for that time, not with the whole day.
            if listed or value.get("dates_not_published") or entry.get("applicability") \
                    or entry.get("current") is False:
                _undated(reply, entry, sources)
            return
        # "Open at 10am?" is followed by the day's hours as always, so a time named for another
        # reason ("I have class at 10am, when is it open?") still gets them.
    if value.get("hours") is None:
        if value.get("status") == "not_published":
            checked = (value.get("absence") or {}).get("checked_at")
            reply.say(subject, f"{subject} doesn't publish hours for {day}"
                      + (f" (checked {_when(checked, year=True)})." if checked else "."), sources)
            return
        if isinstance(value.get("listed_hours"), str):
            if "listed_open_now" in entry and not now_said:
                for sentence in _open_now(subject, entry["listed_open_now"], True):
                    reply.say(subject, sentence, sources)
            reply.say(subject, f"{named} lists {entry['day']} hours of "
                               f"{literal(value['listed_hours'])}.", sources)
            _undated(reply, entry, sources)
            _notes(reply, subject, value.get("notes"), value["listed_hours"], sources)
            return
        reply.say(subject, f"{named} has no verified hours for {day}.", sources)
        return
    if not isinstance(value["hours"], str):
        raise CannotSay
    later: list[str] = []
    for key, listed in (("open_now", False), ("listed_open_now", True)):
        if key in entry and not brief and not now_said:
            first, *later = _open_now(subject, entry[key], listed)
            reply.say(subject, first, sources)
    periods = value.get("periods") or []
    if not isinstance(periods, list) or not all(isinstance(p, dict) for p in periods):
        raise CannotSay
    if value.get("meal_not_served") and value.get("meal_asked"):
        # The day's meal periods are named, and the one asked is not among them (11-03).
        reply.say(subject, f"{named} doesn't list {literal(value['meal_asked']).lower()} on "
                           f"{day}.", sources)
    # One set of hours with no meal names (meals_not_labelled) is said as it is: it neither
    # serves nor skips the meal asked (11-03: the Atrium at lunch went unsaid).
    if value.get("meal_asked") and periods and all(p.get("label") for p in periods):
        for period in periods:
            reply.say(subject, f"{literal(period['label'])} at {named} on {day}: "
                               f"{period['opens']} to {period['closes']}.", sources)
    elif periods and not any(p.get("label") for p in periods):
        spans = ", ".join(f"{p['opens']} to {p['closes']}" for p in periods)
        reply.say(subject, f"{named} on {day}: {spans}.", sources)
    else:
        reply.say(subject, f"{named} on {day}: {literal(value['hours'])}.", sources)
    for sentence in later:
        reply.say(subject, sentence, sources)
    if value.get("dates_not_published") or entry.get("applicability") or entry.get(
            "current") is False:
        _undated(reply, entry, sources)
    _notes(reply, subject, value.get("notes"), value["hours"], sources)


def _not_listed(reply: _Reply, subject: str, value: dict[str, Any], sources: list[int]) -> None:
    for entry in value.get("days") or []:
        if isinstance(entry, dict) and entry.get("hours") is None \
                and entry.get("status") != "not_published":
            reply.not_listed.setdefault((subject, str(entry.get("day"))), set()).update(sources)


def _week(value: dict[str, Any]) -> str:
    """A whole week, next days that share hours said together, as code's text groups them."""
    _known(value, _WEEK_KEYS)
    runs: list[dict[str, Any]] = []
    for entry in value.get("days") or []:
        if not isinstance(entry, dict) or "day" not in entry:
            raise CannotSay
        hours = entry.get("hours")
        said = (literal(hours) if isinstance(hours, str) else
                "not published" if entry.get("status") == "not_published" else "not listed")
        checked = (entry.get("absence") or {}).get("checked_at")
        if runs and runs[-1]["said"] == said:
            runs[-1]["days"].append(entry["day"])
        else:
            runs.append({"said": said, "days": [entry["day"]], "checked": checked})
    if not runs:
        raise CannotSay
    parts = []
    for run in runs:
        days = run["days"]
        span = (days[0] if len(days) == 1 else f"{days[0]} and {days[1]}" if len(days) == 2
                else f"{days[0]} to {days[-1]}")
        checked = (f" (checked {_when(run['checked'], year=True)})"
                   if run["said"] == "not published" and run["checked"] else "")
        parts.append(f"{span}: {run['said']}{checked}")
    return "; ".join(parts)


def _phones(value: Any) -> str:
    if not isinstance(value, list) or not value:
        raise CannotSay
    said = []
    for phone in value:
        if not isinstance(phone, dict) or set(phone) - {"number", "extension", "type", "label",
                                                        "text"}:
            raise CannotSay
        number = phone.get("number") or phone.get("text")
        if not isinstance(number, str):
            raise CannotSay
        # A number is said with its own digits; it is never escaped, so the check can read it.
        text = us_phone(number) if us_phone(number) != number else literal(number)
        if phone.get("extension") is not None:
            text += f" extension {literal(phone['extension'])}"
        label = phone.get("type") or phone.get("label")
        said.append(f"{literal(label)}: {text}" if label else text)
    if len(said) > PHONE_LINES:
        said = [*said[:PHONE_LINES], f"and {_count(len(said) - PHONE_LINES)} more on its page"]
    return "; ".join(said)


def _rooms(value: Any) -> tuple[list[str], list[tuple[str, str]]]:
    """An office's rooms: the plain ones, and the labelled ones as (label, room)."""
    if not isinstance(value, list) or not value:
        raise CannotSay
    plain: list[str] = []
    labelled: list[tuple[str, str]] = []
    for room in value:
        if isinstance(room, str):
            plain.append(literal(room))
        elif isinstance(room, dict) and set(room) <= {"location", "label"} and isinstance(
                room.get("location"), str):
            (labelled.append((literal(room["label"]), literal(room["location"])))
             if room.get("label") else plain.append(literal(room["location"])))
        else:
            raise CannotSay
    return plain, labelled


def _item(item: dict[str, Any], details: frozenset[str] = frozenset()) -> str:
    """A menu item by its published name and its vegan or vegetarian label, with the details
    asked (calories, portion, allergens) as the menu gives them."""
    name = literal(item.get("name", ""))
    label = ("vegan" if item.get("vegan") is True else
             "vegetarian" if item.get("vegetarian") is True else None)
    said = f"{name} ({label})" if label else name
    facts = []
    if "calories" in details and isinstance(item.get("calories"), int):
        facts.append(f"{item['calories']} calories")
    if "portion" in details and item.get("portion"):
        facts.append(f"portion {literal(item['portion'])}")
    if "allergens" in details:
        allergens = item.get("allergens")
        facts.append(f"contains {', '.join(literal(a) for a in allergens)}" if allergens
                     else "no allergens listed")
    return said + (": " + "; ".join(facts) if facts else "")


def _topping(item: dict[str, Any]) -> bool:
    calories = item.get("calories")
    return not isinstance(calories, int) or calories < TOPPING_CALORIES


_BOARD_KEYS = frozenset({"board", "on_board", "read_at", "allergens_listed", "allergens_published",
                         "allergens_not_listed",
                         "meal_asked", "meal_not_on_board", "meals_left_out",
                         "on_board_besides", "nutrients_unlisted",
                         "allergens_unlabelled", "filter", "matched", "allergen_words",
                         "stations_listed", "sections", "detailed", "details_asked",
                         "only_menu_venue"})
_BOARD_ITEM_KEYS = frozenset({"name", "prices", "calories", "marked", "contains", "description",
                              "options", "options_matched"})


def _board_prices(item: dict[str, Any]) -> str:
    """An item's prices as the board prints them: '$9.99, 540 cal' or '6" $9.99, 12" $11.99'."""
    said = []
    for price in item.get("prices") or []:
        if not isinstance(price, dict):
            raise CannotSay
        said.append(" ".join(literal(price[key]) for key in ("size", "price", "calories")
                             if price.get(key)))
    if item.get("calories"):
        said.append(literal(item["calories"]))
    return "; ".join(s for s in said if s)


def _board_line(item: dict[str, Any]) -> str:
    """A board item in a list: its name and its price."""
    _known(item, _BOARD_ITEM_KEYS)
    prices = [literal(p["price"]) for p in item.get("prices") or []
              if isinstance(p, dict) and p.get("price")]
    notes = [" / ".join(prices)] if prices else []
    notes += [literal(o) for o in item.get("options_matched") or []]
    if item.get("marked"):
        notes.append(literal(item["marked"]))
    return literal(item["name"]) + (f" ({', '.join(notes)})" if notes else "")


def _board_detail(item: dict[str, Any], where: str, allergens: bool, marks: bool = True) -> str:
    """One board item in full: where it is, its prices and calories, its description, the
    options matched (or all of a build-your-own item's), and what it says it contains."""
    _known(item, _BOARD_ITEM_KEYS)
    said = f"- {literal(item['name'])} ({where})"
    prices = _board_prices(item)
    said += f": {prices}." if prices else "."
    if item.get("description"):
        described = literal(item["description"])
        said += f" {described[:1].upper()}{described[1:]}."
    if item.get("marked") and marks:
        said += f" The board marks it {literal(item['marked'])}."
    options = [o for o in item.get("options") or [] if isinstance(o, dict)]
    matched = item.get("options_matched")
    if matched:
        options = [o for o in options if o.get("name") in matched]
    if options:
        groups: dict[str, list[str]] = {}
        for option in options:
            extra = [literal(option[k]) for k in ("price", "calories") if option.get(k)]
            extra += [" ".join(literal(p[k]) for k in ("size", "price") if p.get(k))
                      for p in option.get("prices") or [] if isinstance(p, dict)]
            groups.setdefault(literal(option.get("group") or "Choices"), []).append(
                literal(option["name"]) + (f" ({', '.join(extra)})" if extra else ""))
        said += " " + "; ".join(f"{group}: {', '.join(names)}"
                                for group, names in groups.items()) + "."
    if item.get("contains"):
        said += f" Contains {literal(item['contains'])}."
    elif allergens:
        said += " The board lists no allergens for it."
    return said


def _board(subject: str, value: dict[str, Any]) -> list[str]:
    """A venue's menu board (no date): its sections with their items and prices; the items a
    student named in full; a diet or allergen list by the board's own marks and words."""
    _known(value, _BOARD_KEYS)
    sections = value.get("sections")
    if not isinstance(sections, list) or not isinstance(value.get("board"), str):
        raise CannotSay
    filter_ = value.get("filter") or {}
    if not isinstance(filter_, dict):
        raise CannotSay
    said: list[str] = []
    lead = (f"{subject}'s menu board doesn't list a separate "
            f"{literal(value['meal_not_on_board']).lower()} menu. "
            if value.get("meal_not_on_board") else "")
    unchecked = value.get("allergens_not_listed") or 0
    free_of = filter_.get("without_allergens") or []
    if filter_ and not value.get("matched") and free_of and not (
            filter_.get("contains") or filter_.get("diet")):
        words = " or ".join(literal(a) for a in free_of)
        return [f"None of the {value.get('allergens_listed')} items on {subject}'s menu board that "
                f"list allergens is free of {words}."
                + (f" The other {unchecked} items list no allergens, so they can't be checked."
                   if unchecked > 0 else "")]
    if filter_ and not value.get("matched"):
        stations = ", ".join(literal(s) for s in value.get("stations_listed") or [])
        foods = " or ".join(literal(f) for f in filter_.get("contains") or [])
        return [*said, f"Nothing on {subject}'s menu board matches"
                + (f" {foods}" if foods else "")
                + (f"; its stations are {stations}." if stations else ".")]
    if not sections and value.get("allergens_unlabelled") and not filter_:
        labels = [literal(a) for a in value.get("allergens_published") or []]
        if not labels:
            raise CannotSay
        return [*said, f"{subject}'s menu board lists allergens for "
                       f"{_count(value.get('allergens_listed') or 0)} of its "
                       f"{value.get('on_board')} items: {', '.join(labels)}."]
    if not sections:
        raise CannotSay

    def where(section: dict[str, Any]) -> str:
        names = [literal(n) for n in (section.get("station"), section.get("section")) if n]
        if not names:
            raise CannotSay
        return ", ".join(names)

    allergens = bool(filter_.get("with_allergens") or filter_.get("without_allergens")
                     or "allergens" in (value.get("details_asked") or []))
    if value.get("detailed"):
        if filter_.get("contains"):
            foods = " or ".join(literal(f) for f in filter_["contains"])
            head = f"{subject}'s menu board items with {foods}:"
        elif filter_.get("diet"):
            head = (f"{subject}'s menu board marks {_count(value['matched'])} items "
                    f"{'vegan' if filter_['diet'] == 'vegan' else 'vegan or vegetarian'}:")
        elif filter_:
            head = f"{subject}'s menu board items that match:"
        elif value.get("meal_asked"):
            head = (f"{subject}'s menu board, {literal(value['meal_asked']).lower()} items:")
        else:
            raise CannotSay
        lines = [_board_detail(item, where(section), allergens,
                               filter_.get("diet") != "vegan")
                 for section in sections for item in section.get("items") or []]
        said.append(lead + head + "\n" + "\n".join(lines))
    else:
        if filter_:
            head = (f"{subject}'s menu board has {value.get('matched')} items that match, "
                    "with their prices:")
        elif value.get("meals_left_out"):
            # The sections the board names for another meal are not said, nor counted.
            others = " and ".join(literal(m).lower() for m in value["meals_left_out"])
            head = (f"{lead}{'It' if lead else subject + chr(39) + 's menu board'} lists "
                    f"{value.get('on_board_besides')} items besides its {others} sections. Some "
                    "of each section, with prices:")
        else:
            head = (f"{lead}{'It' if lead else subject + chr(39) + 's menu board'} lists "
                    f"{value.get('on_board')} items. Some of each section, with prices:")
        lines = []
        for section in sections:
            items = [_board_line(item) for item in section.get("items") or []]
            if len(items) > MENU_ITEMS:
                items = [*items[:MENU_ITEMS], f"and {_count(len(items) - MENU_ITEMS)} more"]
            lines.append(f"- {where(section)}: {', '.join(items)}")
        said.append(head + "\n" + "\n".join(lines) + (
            "\n\nAsk me about any item for its description, calories and the allergens the "
            "board lists." if not filter_ else ""))
    if allergens and value.get("allergens_listed") is not None:
        if unchecked > 0 and (filter_.get("without_allergens") or filter_.get("with_allergens")):
            said.append(f"The board lists allergens for only {value['allergens_listed']} of its "
                        f"{value.get('on_board')} items, so the others can't be checked.")
    return said


def _menu(subject: str, value: dict[str, Any]) -> list[str]:
    """A date's menu: each meal's stations, one line each, with their first few dishes. Items the
    student named (a filter's foods) are all said, with their details."""
    _known(value, _MENU_KEYS)
    if not isinstance(value.get("date"), str):
        raise CannotSay
    when = _day({"day": f"{date.fromisoformat(value['date']):%A}", "date": value["date"]})
    asked = literal(value["meal_asked"]).lower() + " " if value.get("meal_asked") else ""
    if value.get("meal_not_listed"):
        listed = ", ".join(literal(m) for m in value.get("meals_listed") or [])
        return [f"{subject}'s menu for {when} lists no {literal(value['meal_not_listed'])}"
                + (f"; it lists {listed}." if listed else ".")]
    filter_ = value.get("filter")
    if filter_ is not None and (not isinstance(filter_, dict)
                                or set(filter_) - {"diet", "contains"}):
        raise CannotSay
    named = bool(filter_ and filter_.get("contains"))
    if filter_ and value.get("matched") == 0:
        stations = ", ".join(literal(st) for st in value.get("stations_listed") or [])
        return [f"Nothing on {subject}'s {asked}menu for {when} matches"
                + (f"; its stations are {stations}." if stations else ".")]
    meals = value.get("meals")
    if not isinstance(meals, list) or not meals:
        raise CannotSay
    asked_details = value.get("details_asked") or []
    if not isinstance(asked_details, list):
        raise CannotSay
    details = frozenset(asked_details) | (frozenset({"calories", "allergens"}) if named
                                          else frozenset())
    said = []
    if named and filter_:
        # The foods the student named: each once, with the meals it is served at.
        served: dict[str, tuple[dict[str, Any], list[str]]] = {}
        for meal in meals:
            for station in meal.get("stations") or []:
                for item in station.get("items") or []:
                    if isinstance(item, dict) and item.get("name"):
                        first, at = served.setdefault(str(item["name"]), (item, []))
                        if meal.get("meal") not in at:
                            at.append(meal.get("meal"))
        if not served:
            raise CannotSay
        foods = " or ".join(literal(f) for f in filter_["contains"])
        said.append(f"{subject}'s items with {foods} on {when}:\n" + "\n".join(
            f"- {_item(item, details)} ({', '.join(literal(m).lower() for m in at)})"
            for item, at in served.values()))
        if value.get("matches_cut"):
            said.append(f"These are some of the {value.get('matched')} items that match.")
        return said
    for meal in meals:
        lines = []
        for station in meal.get("stations") or []:
            items, seen = [], set()
            for item in station.get("items") or []:
                if not isinstance(item, dict) or item.get("name") in seen:
                    continue
                seen.add(item.get("name"))
                if _topping(item):
                    continue
                items.append(_item(item, details))
            if not items:
                continue
            if len(items) > MENU_ITEMS:
                items = [*items[:MENU_ITEMS], f"and {_count(len(items) - MENU_ITEMS)} more"]
            lines.append(f"- {literal(station.get('station', ''))}: {', '.join(items)}")
        if not lines:
            raise CannotSay
        meal_name = literal(meal.get("meal", ""))
        if filter_ and filter_.get("diet"):
            head = (f"{subject}'s {meal_name.lower()} on {when} has {value.get('matched')} "
                    f"{literal(filter_['diet'])} items (of {value.get('on_menu')}), such as:")
        else:
            head = f"{subject} {meal_name.lower()} menu for {when}:"
        said.append(head + "\n" + "\n".join(lines))
    if value.get("whole_menu_asked") and value.get("rest_of_menu"):
        rest = [literal(n) for n in value["rest_of_menu"]]
        said.append("Also on the menu: " + ", ".join(rest[:MENU_ITEMS * 3])
                    + (f", and {_count(len(rest) - MENU_ITEMS * 3)} more."
                       if len(rest) > MENU_ITEMS * 3
                       else "."))
    return said


def _fact(reply: _Reply, fact: dict[str, Any]) -> None:
    if not isinstance(fact, dict) or set(fact) - {"subject", "predicate", "value", "sources",
                                                    "purpose", "current"}:
        raise CannotSay
    subject, key, value, sources = (literal(fact["subject"]), fact["predicate"], fact["value"],
                                    fact["sources"])
    if fact.get("current") is False and key not in ("hours", "procedures"):
        raise CannotSay  # A value that may not be current is said with its dates by code's text.
    if key == "hours":
        if not isinstance(value, dict):
            raise CannotSay
        named, week = _schedule_name(value, subject), _week(value)
        dates = _dates(reply, value, fact, sources)
        reply.say(subject, f"{named} hours{f' for {dates}' if dates else ''}: {week}.", sources)
        _not_listed(reply, subject, value, sources)
        _notes(reply, subject, value.get("notes"), week, sources)
    elif key == "phones":
        all_labelled = all(isinstance(p, dict) and (p.get("type") or p.get("label"))
                           for p in value)
        reply.say(subject, f"{subject}{':' if all_labelled else ' phone:'} {_phones(value)}.",
                  sources)
    elif key == "email":
        emails = value if isinstance(value, list) else [value]
        if not emails or not all(isinstance(e, str) for e in emails):
            raise CannotSay
        reply.say(subject, f"{subject} email: {' or '.join(literal(e) for e in emails)}.",
                  sources)
    elif key == "offices":
        plain, labelled = _rooms(value)
        # An office named like its building (a residence hall's): the room is its office's, and
        # the building is where the campus map shows it (11-03: "Pine Hall is in First floor").
        owner = f"{subject}'s office" if subject in reply.buildings else subject
        if plain:
            reply.say(subject, f"{owner} {_located('; '.join(plain))}.", sources)
        if labelled and plain:
            reply.say(subject, "; ".join(f"{label}: {room}" for label, room in labelled) + ".",
                      sources)
        elif labelled:
            # Rooms that are all labelled still say whose they are (11-03: "Main Office: C-209;
            # ASB Office: ASB-513." never named the Career Center).
            where = "; ".join(f"{room} ({label})" for label, room in labelled)
            reply.say(subject, f"{owner} {_located(where)}.", sources)
    elif key == "website":
        if not isinstance(value, str) or citation_url(value) is None:
            raise CannotSay
        reply.say(subject, f"{subject} website: {value}.", sources)
    elif key in _BUILDING_KEYS:
        _building(reply, subject, key, value, sources)
    elif key == "building":
        # Where an office is when its own records publish no room: the building the graph
        # places it in, and where the campus map shows that (11-03: the Bradley Center lounge).
        if not isinstance(value, list) or not value or not all(
                isinstance(b, dict) and isinstance(b.get("name"), str)
                and isinstance(b.get("map"), str) and citation_url(b["map"]) for b in value):
            raise CannotSay
        if len(value) == 1:
            reply.say(subject, f"{subject} is in {literal(value[0]['name'])}, which is on the "
                               f"campus map: {value[0]['map']}.", sources)
        else:
            reply.say(subject, f"{subject} is in {_and([literal(b['name']) for b in value])}.",
                      sources)
            for b in value:
                reply.say(subject, f"{literal(b['name'])} is on the campus map: {b['map']}.",
                          sources)
    elif key in ("contact_note", "department", "name", "preferred_contact"):
        if not isinstance(value, str):
            raise CannotSay
        if key == "contact_note" and not (value := _unsaid(reply, value)):
            return
        reply.say(subject, {"contact_note": f"{subject}: {literal(value)}",
                            "department": f"{subject} is part of {literal(value)}.",
                            "name": f"{subject}'s published name is {literal(value)}.",
                            "preferred_contact":
                                f"{subject} prefers to be reached by: {literal(value)}."}[key],
                  sources)
    elif key == "prefers_email" and value is True:
        reply.say(subject, f"{subject} says it prefers email.", sources)
    elif key == "procedures":
        if not isinstance(value, dict) or not isinstance(value.get("text"), str) \
                or set(value) - {"heading", "page", "text", "text_cut", "page_updated", "old_page"}:
            raise CannotSay
        heading = " ".join(str(value.get("heading") or "").split()).rstrip(":")
        said = (heading, " ".join(value["text"].split()))
        if said in reply.sections_said or not said[1]:
            return
        reply.sections_said.add(said)
        chars = reply.quotes[subject in reply.quoted]
        reply.quoted.add(subject)
        page = f'{subject}\'s page "{literal(heading)}"' if heading else f"{subject}'s page"
        # A page not read again since its freshness ran out (11-03: the clubs pages were last
        # read September 26) is said with the day it was read, never as today's words.
        read = f", as read on {_read_on(reply, sources)}," if fact.get("current") is False else ""
        if not chars:  # A long reply names the page; its words are on it.
            reply.say(subject, f"See {page}" + (f" (read on {_read_on(reply, sources)})"
                                                if read else "") + _old_page(value) + ".",
                      sources)
            return
        excerpt, cut = _excerpt(said[1], chars)
        reply.say(subject, f"{page}{read} says: "
                  + f'"{literal(excerpt)}"' + (" (more on its page)" if cut
                                                or value.get("text_cut") else "")
                  + _old_page(value),
                  sources)
    elif key == "menu" and isinstance(value, dict):
        for sentence in (_board(subject, value) if "board" in value else _menu(subject, value)):
            reply.say(subject, sentence, sources)
    else:
        raise CannotSay


def _words_of(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _unsaid(reply: _Reply, note: str) -> str:
    """A note's sentences the reply has not said yet (11-03 practice: the Library's contact note
    repeats its hours page's "the front doors will be locked fifteen minutes before closing time",
    and the reply said it twice)."""
    said = _words_of(reply.text())
    return " ".join(sentence for sentence in re.split(r"(?<=[.!?])\s+", note.strip())
                    if not (words := _words_of(sentence)) or words not in said)


def _located(where: str) -> str:
    """Where a room is, as a sentence's end: "is in C-209", a place phrase as written ("is
    near the Atrium"), or a floor ("is on the first floor")."""
    lowered = f"{where[0].lower()}{where[1:]}"
    if where.split(" ", 1)[0].casefold() in _PLACE_WORDS:
        return f"is {lowered}"
    if _FLOOR.match(where):
        return f"is on the {lowered}"
    return f"is in {where}"


def _building(reply: _Reply, subject: str, key: str, value: Any, sources: list[int]) -> None:
    """A building's facts (11-03): where the campus map shows it, the room numbers that are in
    it, and its places. A building publishes no hours: asked them, it says so, and its places'
    own hours are said after it."""
    where = "hours" not in reply.fields or "offices" in reply.fields
    if key == "map":
        if not isinstance(value, str) or citation_url(value) is None:
            raise CannotSay
        if where:
            reply.say(subject, f"{subject} is on the campus map: {value}.", sources)
    elif key == "room_prefixes":
        if not isinstance(value, list) or not value or not all(isinstance(p, str) for p in value):
            raise CannotSay
        said = [literal(p) for p in value]
        prefixes = said[0] if len(said) == 1 else f"{', '.join(said[:-1])} or {said[-1]}"
        # "Where is ASB 407?": its prefix is what places the room in this building (11-03).
        rooms = [literal(room) for room in reply.rooms if any(
            re.match(rf"{re.escape(p)}(?![A-Za-z])", room, re.IGNORECASE) for p in value)]
        if where and rooms:
            reply.say(subject, f"{_and(rooms)} {'is' if len(rooms) == 1 else 'are'} in "
                               f"{subject}, where room numbers start with {prefixes}.", sources)
        elif where:
            reply.say(subject, f"Room numbers in {subject} start with {prefixes}.", sources)
    else:
        if not isinstance(value, list) or not value or not all(
                isinstance(p, dict) and isinstance(p.get("name"), str) for p in value):
            raise CannotSay
        names = [str(p["name"]) for p in value]
        if "hours" in reply.fields:
            # Marked by nothing: the places' hours that follow carry their own pages' marks.
            reply.say(subject, (f"I don't have hours for {subject} as a whole, but places in it "
                                "list their own." if reply.with_hours & set(names)
                                else f"I don't have hours for {subject}."), [])
        if where and (not reply.rooms or "places_in" in reply.fields):
            # Asked what is in it, every place is named; beside where it is, a few are; beside
            # where a room in it is, none.
            cut = None if "places_in" in reply.fields else PLACES_SAID
            shown = [literal(n) for n in names[:cut]]
            if cut is not None and len(names) > cut:
                shown.append(f"{_count(len(names) - cut)} more")
            reply.say(subject, f"Places in {subject} include {_and(shown)}.", sources)


def _read_on(reply: _Reply, sources: list[int]) -> str:
    """The day a fact's pages were read, the earliest of them, as a student reads a date."""
    read = sorted(reply.read_on[n] for n in sources if n in reply.read_on)
    if not read:
        raise CannotSay  # Undated, it is left to code's evidence text, which says what it can.
    return _when(read[0], year=reply.year is not None and read[0][:4] != reply.year)


def _old_page(value: dict[str, Any]) -> str:
    """For a section of a page the college has not changed in years: when it last changed, so the
    student can weigh it ("its page was last updated March 2021")."""
    if not value.get("old_page") or not isinstance(value.get("page_updated"), str):
        return ""
    try:
        updated = date.fromisoformat(value["page_updated"])
    except ValueError:
        raise CannotSay from None
    return f" (its page was last updated {updated:%B} {updated.year})"


def _excerpt(text: str, chars: int = SECTION_CHARS) -> tuple[str, bool]:
    """The section's words up to the end of a sentence past `chars`, and whether any were left
    out."""
    if len(text) <= chars:
        return text, False
    end = re.search(r"[.!?](?=\s+[A-Z(\"“])", text[chars:])
    if end is None or end.end() > chars:
        cut = text[:chars * 2].rsplit(" ", 1)[0]
        return (cut + "…", True) if len(cut) < len(text) else (text, False)
    stop = chars + end.end()
    return text[:stop], stop < len(text)


def _listings(group: list[dict[str, Any]], subject_name: str,
              names: list[str]) -> tuple[str, list[int], list[str]]:
    """How a conflict is introduced: the name its lead sentence gives (the one schedule's, or the
    office's when the schedules differ), the sources that sentence is marked with, and each
    value's label. Values one page gives are "One listing" and "Another listing", with the
    page's mark on the lead too; values from different pages are "One page" and "Another page"."""
    subject = literal(subject_name)
    lead = names[0] if len(set(names)) == 1 else subject
    same = len({tuple(item["sources"]) for item in group}) == 1
    kind = "listing" if same else "page"
    labels = []
    for n, named in enumerate(names):
        label = f"{'One' if n == 0 else 'Another'} {kind}"
        if named != lead and _plain(named) != _plain(subject_name):
            label += f", for {named}"
        labels.append(label)
    return lead, (list(group[0]["sources"]) if same else []), labels


def _conflicts(reply: _Reply, facts: list[Any]) -> list[Any]:
    """Hours the office's pages give differently (status conflicting), said as each page gives
    them, schedule by schedule, with no winner picked; the other facts are returned to be said."""
    groups: dict[str, list[dict[str, Any]]] = {}
    rest = []
    for fact in facts:
        if isinstance(fact, dict) and fact.get("status") == "conflicting" \
                and fact.get("predicate") == "hours" and isinstance(fact.get("value"), dict):
            groups.setdefault(str(fact["subject"]), []).append(fact)
        else:
            rest.append(fact)
    for subject_name, group in groups.items():
        subject = literal(subject_name)
        names = [_schedule_name(fact["value"], subject) for fact in group]
        lead, marks, labels = _listings(group, subject_name, names)
        reply.say(subject, f"The {'page lists' if marks else 'pages list'} different {lead} "
                           "hours.", marks)
        for fact, label in zip(group, labels, strict=True):
            value = {k: v for k, v in fact["value"].items() if k not in ("schedule", "season")}
            dates = _dates(reply, fact["value"], fact, fact["sources"])
            week = _week(value)
            reply.say(subject, f"{label}{f', for {dates}' if dates else ''}: {week}.",
                      fact["sources"])
            _not_listed(reply, subject, value, fact["sources"])
    return rest


def _day_conflicts(reply: _Reply, entries: list[Any], menus: set[Any]) -> list[Any]:
    """One day's hours the pages give differently (derived facts with status conflicting: the IT
    Help Desk's page lists 8:00am-8:00pm and 8:30am-8:00pm for a Tuesday), said listing by
    listing in the page's words with no winner picked. Whether the office is open now is said
    when every listing says the same. The other derived facts are returned to be said."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    rest = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("status") == "conflicting" \
                and entry.get("predicate") == "hours_on":
            groups.setdefault((str(entry["subject"]), str(entry["date"])), []).append(entry)
        else:
            rest.append(entry)
    for (subject_name, _), every in groups.items():
        group = [entry for entry in every if entry.get("applies") is not False]
        for entry in group:
            _known(entry, _DERIVED_KEYS | {"status"})
            _known(entry["value"], _DAY_KEYS)
        if not group:
            continue
        subject = literal(subject_name)
        reply.days_said.add((subject, group[0]["day"]))
        names = [_schedule_name(entry["value"], subject) for entry in group]
        lead, marks, labels = _listings(group, subject_name, names)
        every_source = list(dict.fromkeys(n for entry in group for n in entry["sources"]))
        for key, listed in (("open_now", False), ("listed_open_now", True)):
            nows = [entry.get(key) for entry in group]
            if subject_name in menus or not all(isinstance(now, dict) for now in nows):
                continue
            said = [_open_now(subject, now, listed) for now in nows if isinstance(now, dict)]
            agreed = said[0] if all(s == said[0] for s in said) else (
                said[0][:1] if all(s[0] == said[0][0] for s in said) else [])
            for sentence in agreed:
                reply.say(subject, sentence, every_source)
        reply.say(subject, f"The {'page lists' if marks else 'pages list'} different {lead} "
                           f"hours for {_day(group[0])}.", marks)
        for entry, label in zip(group, labels, strict=True):
            value = entry["value"]
            hours = value.get("hours") if value.get("hours") is not None \
                else value.get("listed_hours")
            if hours is not None and not isinstance(hours, str):
                raise CannotSay
            said_hours = (literal(hours) if hours else "not published"
                          if value.get("status") == "not_published" else "not listed")
            reply.say(subject, f"{label}: {said_hours}.", entry["sources"])
            if value.get("dates_not_published") or entry.get("applicability") \
                    or entry.get("current") is False:
                _undated(reply, entry, entry["sources"])
            _notes(reply, subject, value.get("notes"), str(hours or ""), entry["sources"])
    return rest


# What an office's pages list more than one of, said before each value (Degree Completion's
# rooms: the director's ASB-435 and ASB-424).
_MORE_THAN_ONE = {"offices": "room", "phones": "phone number", "email": "email address",
                  "website": "website"}


def _facts(reply: _Reply, facts: list[Any], also: bool) -> None:
    """The facts said in turn (`also`: the ones published beside the answer). A value the pages
    give differently is said after a sentence that says they list more than one, with no
    winner picked."""
    led: set[tuple[str, str]] = set()
    for fact in facts:
        if not isinstance(fact, dict) or (fact.get("purpose") == "also_published") != also:
            continue
        if "status" in fact:
            thing = _MORE_THAN_ONE.get(str(fact.get("predicate")))
            if fact["status"] != "conflicting" or thing is None:
                raise CannotSay
            subject = literal(fact["subject"])
            if (subject, fact["predicate"]) not in led:
                led.add((subject, fact["predicate"]))
                group = [f for f in facts if isinstance(f, dict) and f.get("status")
                         == "conflicting" and f.get("subject") == fact["subject"]
                         and f.get("predicate") == fact["predicate"]]
                same = len({tuple(f.get("sources") or []) for f in group}) == 1
                reply.say(subject, f"The {'page lists' if same else 'pages list'} more than one "
                                   f"{thing} for {subject}.",
                          list(fact["sources"]) if same else [])
            fact = {k: v for k, v in fact.items() if k != "status"}
        _fact(reply, fact)


# A gap in a schedule: which schedule (and season) it is of, the record's own reason, the days it
# leaves unknown, and the proof that the schedule's dates are not published.
_GAP_KEYS = frozenset({"subject", "predicate", "reason", "sources", "schedule", "status",
                       "details", "days", "source_statements", "season", "validity_absence"})


def _unknown_days(reply: _Reply, subject: str, gap: dict[str, Any]) -> None:
    """Weekdays of a schedule with no verified hours. Not said when a day was asked (a derived
    fact said it: the week's other days are not what was asked), nor when the week said those
    days were not listed. A reason that holds hours nothing else said ("Normal: 8:30 a.m. to 4:30
    p.m.", which names no days) is said in its own words."""
    sources = gap.get("sources") or []
    days = gap.get("days") or []
    said = all(set(sources) <= reply.not_listed.get((subject, str(d)), set()) for d in days)
    if days and not said and not any(asked[0] == subject for asked in reply.days_said):
        season = gap.get("season")
        named = subject + (f" ({literal(season)})" if season
                           and str(season).casefold() not in subject.casefold() else "")
        reply.say(subject, f"{named} has no verified hours for {_days(days)}.", sources)
    details = gap.get("details")
    if isinstance(details, str) and _numbers(details) - _numbers(reply.text()):
        reply.say(subject, f'Its listing adds: "{literal(details.strip())}"', sources)


def _open_then(entry: Any) -> bool:
    """Whether a day's hours are open at or after the time the student asked about."""
    at = entry.get("open_at") or entry.get("listed_open_at") if isinstance(entry, dict) else None
    return isinstance(at, dict) and at.get("state") == "open"


def plain_text(view: dict[str, Any], limit: int | None = None) -> str | None:
    """The reply in plain sentences with source marks, or None when the view holds a shape this
    writer does not know (an ambiguity, a conflict, a shuttle timetable, a menu filter...). A
    reply longer than `limit` quotes its page sections shorter, step by step, then names their
    pages only; it is never cut."""
    text = None
    for quotes in QUOTE_STEPS:
        text = _plain_text(view, quotes)
        if text is None or limit is None or len(text) < limit:
            return text
    return text


def _plain_text(view: dict[str, Any], quotes: tuple[int, int]) -> str | None:
    try:
        if not isinstance(view, dict) or set(view) - _VIEW_KEYS:
            raise CannotSay
        reply = _Reply(view, quotes)
        facts = view.get("facts") or []
        menus = {f.get("subject") for f in facts if isinstance(f, dict)
                 and f.get("predicate") == "menu"}
        facts = _conflicts(reply, facts)
        # A building is said before the places in it whose hours follow; asked where a room in
        # it is, the room's building comes first, then where the campus map shows it.
        building = [f for f in facts if isinstance(f, dict)
                    and f.get("predicate") in _BUILDING_KEYS]
        if reply.rooms:
            building.sort(key=lambda f: f.get("predicate") != "room_prefixes")
        _facts(reply, building, also=False)
        facts = [f for f in facts if not isinstance(f, dict)
                 or f.get("predicate") not in _BUILDING_KEYS]
        days = _day_conflicts(reply, view.get("derived_facts") or [], menus)
        # Asked about a time ("after 9pm"), the places open then are said first.
        days.sort(key=lambda entry: not _open_then(entry))
        for entry in days:
            if not isinstance(entry, dict) or entry.get("predicate") != "hours_on":
                raise CannotSay
            _day_hours(reply, entry, brief=entry.get("subject") in menus)
        _facts(reply, facts, also=False)
        for absence in view.get("not_published") or []:
            _known(absence, _ABSENCE_KEYS)
            thing = _NOT_PUBLISHED.get(absence["predicate"])
            if thing is None or not isinstance(absence.get("checked_at"), str):
                raise CannotSay
            subject = literal(absence["subject"])
            reply.say(subject, f"{subject} doesn't publish {thing} on its own pages (checked "
                               f"{_when(absence['checked_at'], year=True)}).",
                      absence.get("sources") or [])
        for gap in view.get("missing") or []:
            if not isinstance(gap, dict) or set(gap) - _GAP_KEYS:
                raise CannotSay
            subject = literal(gap["subject"])
            if gap.get("reason") == "partially_unknown_schedule":
                _unknown_days(reply, subject, gap)
            elif gap.get("reason") == "no_fax_line":
                reply.say(subject, f"None of {subject}'s published phone lines is a fax.", [])
            else:
                thing = _UNKNOWN.get(gap["predicate"], str(gap["predicate"]).replace("_", " "))
                reply.say(subject, f"I don't have verified information about {subject}'s "
                                   f"{literal(thing)}.", [])
        _facts(reply, facts, also=True)
        text = reply.text()
        return text or None
    except (CannotSay, KeyError, TypeError):
        return None
