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
# The menu marks no item a topping or a condiment, so an item under this many calories (onions,
# lettuce, pickle chips, ginger) is left out, unless the student named that food.
TOPPING_CALORIES = 50
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

    def __init__(self) -> None:
        self.subjects: dict[str, dict[str, list[int]]] = {}
        self.days_said: set[tuple[str, str]] = set()
        self.dates_note: str | None = None
        self.dates_sources: list[int] = []
        # A page section two subjects share (Public Safety's emergency and non-emergency lines
        # link the same pages) is said once.
        self.sections_said: set[tuple[str, str]] = set()

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
        if key in entry and not brief:
            first, *later = _open_now(subject, entry[key], listed)
            reply.say(subject, first, sources)
    periods = value.get("periods") or []
    if not isinstance(periods, list) or not all(isinstance(p, dict) for p in periods):
        raise CannotSay
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
                         "meal_asked", "meal_not_on_board", "nutrients_unlisted",
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
    elif key == "procedures":
        if not isinstance(value, dict) or not isinstance(value.get("text"), str) \
                or set(value) - {"heading", "page", "text", "text_cut"}:
            raise CannotSay
        heading = " ".join(str(value.get("heading") or "").split()).rstrip(":")
        said = (heading, " ".join(value["text"].split()))
        if said in reply.sections_said or not said[1]:
            return
        reply.sections_said.add(said)
        excerpt, cut = _excerpt(said[1])
        reply.say(subject, (f'{subject}\'s page "{literal(heading)}" says: ' if heading
                            else f"{subject}'s page says: ")
                  + f'"{literal(excerpt)}"' + (" (more on its page)" if cut
                                                or value.get("text_cut") else ""),
                  sources)
    elif key == "menu" and isinstance(value, dict):
        for sentence in (_board(subject, value) if "board" in value else _menu(subject, value)):
            reply.say(subject, sentence, sources)
    else:
        raise CannotSay


# A page section is said in its own words, up to the end of the sentence that passes this many
# characters, and the rest is left on its page.
SECTION_CHARS = 500


def _excerpt(text: str) -> tuple[str, bool]:
    """The section's words up to the end of a sentence past SECTION_CHARS, and whether any were
    left out."""
    if len(text) <= SECTION_CHARS:
        return text, False
    end = re.search(r"[.!?](?=\s+[A-Z(\"“])", text[SECTION_CHARS:])
    if end is None or end.end() > SECTION_CHARS:
        cut = text[:SECTION_CHARS * 2].rsplit(" ", 1)[0]
        return (cut + "…", True) if len(cut) < len(text) else (text, False)
    stop = SECTION_CHARS + end.end()
    return text[:stop], stop < len(text)


def _conflicts(reply: _Reply, facts: list[Any]) -> list[Any]:
    """Hours the office's pages give differently (status conflicting), said as each page gives
    them, schedule by schedule, with no winner picked; the other facts are returned to be said."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    rest = []
    for fact in facts:
        if isinstance(fact, dict) and fact.get("status") == "conflicting" \
                and fact.get("predicate") == "hours" and isinstance(fact.get("value"), dict):
            groups.setdefault((str(fact["subject"]), str(fact["value"].get("schedule"))),
                              []).append(fact)
        else:
            rest.append(fact)
    for (subject_name, schedule), group in groups.items():
        subject = literal(subject_name)
        season = group[0]["value"].get("season")
        named = literal(schedule) + (f" ({literal(season)})" if season and season not in schedule
                                     else "")
        reply.say(subject, f"The pages list different {named} hours.", [])
        for n, fact in enumerate(group):
            value = {k: v for k, v in fact["value"].items() if k not in ("schedule", "season")}
            reply.say(subject, f"{'One page' if n == 0 else 'Another page'}: {_week(value)}.",
                      fact["sources"])
            if value.get("dates_not_published") or fact.get("current") is False:
                _undated(reply, {}, fact["sources"])
    return rest


def plain_text(view: dict[str, Any]) -> str | None:
    """The reply in plain sentences with source marks, or None when the view holds a shape this
    writer does not know (an ambiguity, a conflict, a shuttle timetable, a menu filter...)."""
    try:
        if not isinstance(view, dict) or set(view) - _VIEW_KEYS:
            raise CannotSay
        reply = _Reply()
        facts = view.get("facts") or []
        menus = {f.get("subject") for f in facts if isinstance(f, dict)
                 and f.get("predicate") == "menu"}
        facts = _conflicts(reply, facts)
        for entry in view.get("derived_facts") or []:
            if not isinstance(entry, dict) or entry.get("predicate") != "hours_on":
                raise CannotSay
            _day_hours(reply, entry, brief=entry.get("subject") in menus)
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
