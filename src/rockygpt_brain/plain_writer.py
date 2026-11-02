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
                           "listed_open_now", "applies", "applicability", "applicability_reason",
                           "current"})
_DAY_KEYS = frozenset({"schedule", "hours", "periods", "notes", "season", "status", "absence",
                       "dates_not_published", "listed_hours", "closed_gaps", "meal_asked",
                       "window"})
_OPEN_KEYS = frozenset({"state", "now", "closes", "meal", "meal_ends", "then_meal", "reopens",
                        "reopens_meal", "last_closes", "next_meal", "next_opens"})
_WEEK_KEYS = frozenset({"schedule", "days", "notes", "season", "dates_not_published"})
_MENU_KEYS = frozenset({"date", "meals", "meal_asked"})
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
WHOLE_DAY_ITEMS = 3  # A whole day's menu names a few items of each station, as the writer did.
# A location published as a phrase ("Near the campus South Gate entrance") is said as one.
_PLACE_WORDS = frozenset({"near", "at", "in", "on", "behind", "next", "across", "inside",
                          "outside", "off", "beside", "between", "above", "below"})


def literal(value: Any) -> str:
    """Published text as text: no link, emphasis, heading or HTML can come from a page, while
    parentheses and "&" read as written (answers.literal escapes those too, for its evidence
    text)."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_\[\]#|])", r"\\\1", text)


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


class _Reply:
    """Sentences by subject in the order first said, each with its source marks merged."""

    def __init__(self) -> None:
        self.subjects: dict[str, dict[str, list[int]]] = {}
        self.days_said: set[tuple[str, str]] = set()
        self.dates_note: str | None = None
        self.dates_sources: list[int] = []

    def say(self, subject: str, sentence: str, sources: list[int]) -> None:
        said = self.subjects.setdefault(subject, {})
        marks = said.setdefault(sentence, [])
        marks.extend(n for n in sources if n not in marks)

    def text(self) -> str:
        paragraphs = []
        for said in self.subjects.values():
            marks = {tuple(m) for m in said.values()}
            if len(marks) == 1:
                paragraphs.append(f"{' '.join(said)} {_marks(list(marks.pop()))}".strip())
            elif said:
                paragraphs.append(" ".join(f"{sentence} {_marks(m)}".strip()
                                           for sentence, m in said.items()))
        if self.dates_note:
            paragraphs.append(f"{self.dates_note} {_marks(self.dates_sources)}".strip())
        return "\n\n".join(paragraphs)


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


def _day_hours(reply: _Reply, entry: dict[str, Any]) -> None:
    """One office's or venue's hours on the day asked (a derived hours_on fact)."""
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
    if value.get("hours") is None:
        if value.get("status") == "not_published":
            checked = (value.get("absence") or {}).get("checked_at")
            reply.say(subject, f"{subject} doesn't publish hours for {day}"
                      + (f" (checked {_when(checked, year=True)})." if checked else "."), sources)
            return
        if isinstance(value.get("listed_hours"), str):
            if "listed_open_now" in entry:
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
        if key in entry:
            first, *later = _open_now(subject, entry[key], listed)
            reply.say(subject, first, sources)
    periods = value.get("periods") or []
    if not isinstance(periods, list) or not all(isinstance(p, dict) for p in periods):
        raise CannotSay
    if value.get("meal_asked") and periods and all(p.get("label") for p in periods):
        for period in periods:
            reply.say(subject, f"{literal(period['label'])} on {day}: {period['opens']} to "
                               f"{period['closes']}.", sources)
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
    return "; ".join(said)


def _rooms(value: Any) -> tuple[list[str], list[str]]:
    if not isinstance(value, list) or not value:
        raise CannotSay
    plain, labelled = [], []
    for room in value:
        if isinstance(room, str):
            plain.append(literal(room))
        elif isinstance(room, dict) and set(room) <= {"location", "label"} and isinstance(
                room.get("location"), str):
            (labelled.append(f"{literal(room['label'])}: {literal(room['location'])}")
             if room.get("label") else plain.append(literal(room["location"])))
        else:
            raise CannotSay
    return plain, labelled


def _menu(subject: str, value: dict[str, Any]) -> list[str]:
    _known(value, _MENU_KEYS)
    meals = value.get("meals")
    if not isinstance(meals, list) or not meals or not isinstance(value.get("date"), str):
        raise CannotSay
    when = _when(value["date"])
    said = []
    for meal in meals:
        stations = []
        for station in meal.get("stations") or []:
            items = []
            for item in station.get("items") or []:
                name = literal(item.get("name", ""))
                label = ("vegan" if item.get("vegan") is True else
                         "vegetarian" if item.get("vegetarian") is True else None)
                items.append(f"{name} ({label})" if label else name)
            if not value.get("meal_asked") and len(items) > WHOLE_DAY_ITEMS:
                items = [*items[:WHOLE_DAY_ITEMS], f"and {len(items) - WHOLE_DAY_ITEMS} more"]
            stations.append(f"{literal(station.get('station', ''))}: {', '.join(items)}")
        if not stations:
            raise CannotSay
        said.append(f"{subject} {literal(meal.get('meal', ''))} menu for {when}: "
                    + "; ".join(stations) + ".")
    return said


def _fact(reply: _Reply, fact: dict[str, Any]) -> None:
    if not isinstance(fact, dict) or set(fact) - {"subject", "predicate", "value", "sources",
                                                    "purpose", "current"}:
        raise CannotSay
    subject, key, value, sources = (literal(fact["subject"]), fact["predicate"], fact["value"],
                                    fact["sources"])
    if fact.get("current") is False and key != "hours":
        raise CannotSay  # A value that may not be current is said with its dates by code's text.
    if key == "hours":
        if not isinstance(value, dict):
            raise CannotSay
        schedule, season = value.get("schedule"), value.get("season")
        named = literal(schedule) if schedule else subject
        if season and str(season) not in str(schedule or ""):
            named += f" ({literal(season)})"
        week = _week(value)
        reply.say(subject, f"{named} hours: {week}.", sources)
        if value.get("dates_not_published") or fact.get("current") is False:
            if not value.get("dates_not_published") and not season:
                raise CannotSay
            _undated(reply, {}, sources)
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
        if plain:
            where = "; ".join(plain)
            first = where.split(" ", 1)[0].casefold()
            phrase = first in _PLACE_WORDS
            reply.say(subject, (f"{subject} is {where[0].lower()}{where[1:]}." if phrase
                                else f"{subject} is in {where}."), sources)
        if labelled:
            reply.say(subject, f"{'; '.join(labelled)}.", sources)
    elif key == "website":
        if not isinstance(value, str) or citation_url(value) is None:
            raise CannotSay
        reply.say(subject, f"{subject} website: {value}.", sources)
    elif key in ("contact_note", "department", "name", "preferred_contact"):
        if not isinstance(value, str):
            raise CannotSay
        reply.say(subject, {"contact_note": f"{subject}: {literal(value)}",
                            "department": f"{subject} is part of {literal(value)}.",
                            "name": f"{subject}'s published name is {literal(value)}.",
                            "preferred_contact":
                                f"{subject} prefers to be reached by: {literal(value)}."}[key],
                  sources)
    elif key == "prefers_email" and value is True:
        reply.say(subject, f"{subject} says it prefers email.", sources)
    elif key == "menu" and isinstance(value, dict):
        for sentence in _menu(subject, value):
            reply.say(subject, sentence, sources)
    else:
        raise CannotSay


def plain_text(view: dict[str, Any]) -> str | None:
    """The reply in plain sentences with source marks, or None when the view holds a shape this
    writer does not know (an ambiguity, a conflict, a shuttle timetable, a menu filter...)."""
    try:
        if not isinstance(view, dict) or set(view) - _VIEW_KEYS:
            raise CannotSay
        reply = _Reply()
        for entry in view.get("derived_facts") or []:
            if not isinstance(entry, dict) or entry.get("predicate") != "hours_on":
                raise CannotSay
            _day_hours(reply, entry)
        facts = view.get("facts") or []
        for fact in facts:
            if fact.get("purpose") != "also_published":
                _fact(reply, fact)
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
            if not isinstance(gap, dict) or set(gap) - {
                    "subject", "predicate", "reason", "sources", "schedule", "status", "details",
                    "days", "source_statements"}:
                raise CannotSay
            subject = literal(gap["subject"])
            if gap.get("reason") == "partially_unknown_schedule":
                # Some weekdays of a week's schedule have no verified hours. When a day was asked
                # (a derived fact said it), the week's other days are not what was asked.
                days = gap.get("days") or []
                if days and not any(said[0] == subject for said in reply.days_said):
                    reply.say(subject, f"{subject} has no verified hours for "
                                       f"{' and '.join(literal(d) for d in days)}.",
                              gap.get("sources") or [])
            elif gap.get("reason") == "no_fax_line":
                reply.say(subject, f"None of {subject}'s published phone lines is a fax.", [])
            else:
                thing = _UNKNOWN.get(gap["predicate"], str(gap["predicate"]).replace("_", " "))
                reply.say(subject, f"I don't have verified information about {subject}'s "
                                   f"{literal(thing)}.", [])
        for fact in facts:
            if fact.get("purpose") == "also_published":
                _fact(reply, fact)
        text = reply.text()
        return text or None
    except (CannotSay, KeyError, TypeError):
        return None
