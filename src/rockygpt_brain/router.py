"""A plain lookup routed by code, with no model call.

Dan 11-02: when a question names one office or dining venue by its published name (or one of its
listed other names) and asks only for its contacts, its place, its hours or its menu, code picks
the lookup the model would have picked, and code writes the answer too: no AI call at all.
Anything else is left to the model: no name or two, a name two places share, a word left over
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
                        r"late night)\b")),
    ("contact", re.compile(r"\b(?:contact|reach)\b")),
)
_MEALS = ("late night", "breakfast", "brunch", "lunch", "dinner")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TODAY = frozenset({"today", "tonight", "now"})
# Words a plain lookup question is made of besides the name and the day. Any other word means it
# asks something more ("can I book an appointment", "for grad students"): the model reads it.
_PLAIN = frozenset("""
a an the is are what whats when where how do does did can could i me my for of at to in on it its
there please ramapo college office offices right now today tonight tomorrow s time number numbers
phone phones telephone email e mail address hours open opens close closes closing closed contact
reach located location room building menu menus serving serve serves breakfast brunch lunch dinner
late night fax call get find
""".split())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold().replace("’", "'").replace("'", ""))


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
                if key := tuple(_words(str(name))):
                    names.setdefault(key, set()).add((kind, str(entry["name"])))
    # Every place a name is said, the longest first; a name inside a longer one is not a name.
    spans: list[tuple[int, int, set[tuple[str, str]]]] = []
    for key, owners in names.items():
        for start in range(len(words) - len(key) + 1):
            if tuple(words[start:start + len(key)]) == key:
                spans.append((start, start + len(key), owners))
    spans.sort(key=lambda span: span[0] - span[1])
    covered: set[int] = set()
    found: set[tuple[str, str]] = set()
    for start, end, owners in spans:
        if covered & set(range(start, end)):
            continue
        covered.update(range(start, end))
        found |= owners
    if len(found) != 1:
        return None  # No name, two places, or a name two places share.
    (kind, name), = found
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
    if not fields or ("menu" in fields and kind != "venue"):
        return None
    days = [w for w in rest if w in _WEEKDAYS]
    day = (days[0] if len(days) == 1 else None if days else
           "tomorrow" if "tomorrow" in rest else "today" if _TODAY & set(rest) else None)
    meal = next((m for m in _MEALS if re.search(rf"\b{m}\b", text)), None)
    request: dict[str, Any] = {"query": name, "fields": fields}
    if day or ("menu" in fields and meal):
        request["day"] = day or "today"
    if "fax" in asked:
        request["phone_kind"] = "fax"
    if meal and "menu" in fields:
        request["meal"] = meal
    return {"requests": [request]}
