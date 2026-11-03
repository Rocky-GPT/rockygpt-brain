"""A plain lookup routed by code, with no model call.

Dan 11-02: when a question names one office or dining venue by its published name (or one of its
listed other names) and asks only for its contacts, its place, its hours or its menu, code picks
the lookup the model would have picked, and code writes the answer too: no AI call at all.
A name a few offices share ("Public Safety", emergency and non-emergency) looks up each. Anything
else is left to the model: no name or two, a name an office and a venue share, a word left over
that this does not understand, an earlier message the question could refer back to. Code routes
only what it is sure of.
"""

import re
from collections.abc import Sequence
from typing import Any

# What is asked, by its plain words. "contact" is the three ways to reach an office.
_ASKS = (
    ("fax", re.compile(r"\bfax\b")),
    ("phones", re.compile(r"\b(?:phone|phones|telephone|number|numbers|call)\b")),
    ("email", re.compile(r"\be ?mail\b")),
    ("offices", re.compile(r"\b(?:where|located|location|room|building)\b")),
    ("hours", re.compile(r"\b(?:hours|open|opens|close|closes|closing|closed)\b")),
    ("menu", re.compile(r"\b(?:menu|menus|serving|serve|serves|breakfast|brunch|lunch|dinner|"
                        r"late night|food|foods|eat|price|prices|cost|costs|sell|sells)\b")),
    ("contact", re.compile(r"\b(?:contact|reach)\b")),
)
_MEALS = ("late night", "breakfast", "brunch", "lunch", "dinner")
_MENU_ONLY = re.compile(r"\b(?:menu|menus|breakfast|brunch|lunch|dinner|late night)\b")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TODAY = frozenset({"today", "tonight", "now"})
# Words a plain lookup question is made of besides the name and the day. Any other word means it
# asks something more ("can I book an appointment", "for grad students"): the model reads it.
_PLAIN = frozenset("""
a an the is are what whats when where how do does did can could i me my for of at to in on it its
there please ramapo college office offices right now today tonight tomorrow s time number numbers
phone phones telephone email e mail address hours open opens close closes closing closed contact
reach located location room building menu menus serving serve serves breakfast brunch lunch dinner
late night fax call get find and its their food foods eat price prices cost costs sell sells
""".split())


def _words(text: str) -> list[str]:
    """A text's words, a possessive dropped ("the Registrar's office" names the Registrar)."""
    folded = re.sub(r"(?<=\w)'s\b", "", text.casefold().replace("’", "'"))
    return re.findall(r"[a-z0-9]+", folded.replace("'", ""))


def code_route(message: str, offices: Sequence[dict[str, Any]] | None,
               venues: Sequence[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The graph_lookup arguments for a plain lookup of one listed office or venue, or None."""
    words = _words(message)
    if not words or len(words) > 16:
        return None
    names: dict[tuple[str, ...], set[tuple[str, str]]] = {}
    for kind, listed in (("office", offices or []), ("venue", venues or [])):
        for entry in listed:
            if entry.get("topic"):
                continue  # A subject with no office of its own: the model reads its page.
            for name in [entry["name"], *entry.get("also_called", [])]:
                # An acronym ("ITS", "EHS") is a name only in capitals: "its hours" is not ITS.
                if re.fullmatch(r"[A-Z]{2,6}", str(name).strip()) and not re.search(
                        rf"\b{re.escape(str(name).strip())}\b", message):
                    continue
                if key := tuple(_words(str(name))):
                    names.setdefault(key, set()).add((kind, str(entry["name"])))
                    if key[0] == "the" and len(key) > 1:
                        # "Atrium menu" names The Atrium: "the" is no part of what it is called.
                        names.setdefault(key[1:], set()).add((kind, str(entry["name"])))
    # Every place a name is said, the longest first; a name inside a longer one is not a name.
    spans: list[tuple[int, int, set[tuple[str, str]]]] = []
    for key, owners in names.items():
        for start in range(len(words) - len(key) + 1):
            if tuple(words[start:start + len(key)]) == key:
                spans.append((start, start + len(key), owners))
    spans.sort(key=lambda span: span[0] - span[1])
    covered: set[int] = set()
    said: list[set[tuple[str, str]]] = []
    for start, end, owners in spans:
        if covered & set(range(start, end)):
            continue
        covered.update(range(start, end))
        said.append(owners)
    # A menu asked of no place ("what's for lunch today?") is every venue's that publishes one,
    # as the lookups read it for the model too.
    menus = {("venue", str(v["name"])) for v in venues or []
             if "menu" in (v.get("publishes") or [])}
    if not said and menus and _MENU_ONLY.search(" ".join(words)):
        said = [menus]
    # One name said, of one place or of a few offices that share it ("Public Safety" is both
    # its emergency and non-emergency lines, and both are looked up); two names, or an office
    # and a venue sharing one, go to the model.
    if len(said) != 1 or not 1 <= len(said[0]) <= 3 or len({k for k, _ in said[0]}) != 1:
        return None
    places = sorted(said[0])
    kind = places[0][0]
    rest = [w for k, w in enumerate(words) if k not in covered]
    if any(w not in _PLAIN and w not in _WEEKDAYS for w in rest):
        return None
    text = " ".join(rest)
    asked = [field for field, pattern in _ASKS if pattern.search(text)]
    fields: list[str] = []
    for field in asked:
        fields += (["email", "phones", "offices"] if field == "contact" else
                   ["phones"] if field == "fax" else [field])
    fields = list(dict.fromkeys(fields))
    if not fields or ("menu" in fields and kind != "venue") or (
            not covered and fields != ["menu"]):
        return None
    days = [w for w in rest if w in _WEEKDAYS]
    day = (days[0] if len(days) == 1 else None if days else
           "tomorrow" if "tomorrow" in rest else "today" if _TODAY & set(rest) else None)
    meal = next((m for m in _MEALS if re.search(rf"\b{m}\b", text)), None)
    request: dict[str, Any] = {"fields": fields}
    if day or ("menu" in fields and meal):
        request["day"] = day or "today"
    if "fax" in asked:
        request["phone_kind"] = "fax"
    if meal and "menu" in fields:
        request["meal"] = meal
    return {"requests": [{"query": name, **request} for _, name in places]}


# A plain shuttle question naming no stop or route ("when is the last shuttle tonight?", "what
# time does the first bus leave campus tomorrow?"): the timetable lookup the model would pick.
# Every trip leaves from campus, so no stop is any trip. A stop, a route, a clock time or any other
# word is left to the model, which matches places to the published stops.
_SHUTTLE = re.compile(r"\b(?:shuttles?|bus|buses)\b")
_WHICH = (("next", re.compile(r"\bnext\b")), ("first", re.compile(r"\b(?:first|earliest)\b")),
          ("last", re.compile(r"\b(?:last|latest)\b")))
_SHUTTLE_PLAIN = frozenset("""
when what whats is are does do the a time shuttle shuttles bus buses next first last earliest
latest leave leaves leaving depart departs departure come comes run runs running from campus
ramapo college today tonight tomorrow on this
""".split())


def shuttle_route(message: str) -> dict[str, Any] | None:
    """The shuttle_lookup arguments for a plain next, first or last shuttle question, or None."""
    words = _words(message)
    text = " ".join(words)
    which = [name for name, pattern in _WHICH if pattern.search(text)]
    days = [w for w in words if w in _WEEKDAYS]
    if (not _SHUTTLE.search(text) or len(which) != 1 or len(days) > 1
            or any(w not in _SHUTTLE_PLAIN and w not in _WEEKDAYS for w in words)):
        return None
    day = days[0] if days else "tomorrow" if "tomorrow" in words else None
    return {"requests": [{"stop": None, "which": which[0], "day": day}]}
