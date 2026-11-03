"""Academic calendar dates: which term and which kind of date a question asks about, and code's
answer from the calendar's own entries.

Ramapo's academic calendar pages list each term's dates ("Fall 2026": classes begin, the add/drop
and withdrawal deadlines, breaks, final exams), each entry with its title and day as published and
the kind of date it is (rockygpt-data calendar-concepts.ts). A question asking only when one kind
of date is ("when are finals?", "last day to withdraw this semester", "when does the spring
semester start?") is answered by code: every entry of that kind in the term asked, in date order,
with no model call. Anything else, and a kind the term lists no entry of, is left to the model,
which reads the calendar pages.
"""

import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from rockygpt_brain.plain_writer import literal

# The kind of date a question asks about, by its words; a question naming two kinds is the model's.
_KINDS = (
    ("add_drop_deadline", re.compile(r"\badd\s*(?:/|and|or)?\s*drop\b|\bdrop (?:a |my )?class"
                                     r"(?:es)?\b|\blast day to (?:add|drop)\b")),
    ("withdrawal_deadline", re.compile(r"\bwithdraw\w*\b")),
    ("tuition_refund_deadline", re.compile(r"\brefunds?\b")),
    ("finals", re.compile(r"\bfinals?\b|\bfinal exam\w*\b|\bexam week\b")),
    ("grades_due", re.compile(r"\bgrades? (?:are )?due\b")),
    ("break", re.compile(r"\bbreak\b|\bthanksgiving\b|\brecess\b")),
    ("classes_begin", re.compile(r"\b(?:classes|semester|term) (?:start|starts|begin|begins)\b|"
                                 r"\bfirst day of (?:classes|the semester)\b|"
                                 r"\bstart of (?:the )?(?:semester|term)\b")),
    ("classes_end", re.compile(r"\b(?:classes|semester|term) (?:end|ends|finish|finishes|over)\b|"
                               r"\blast day of (?:classes|the semester)\b|"
                               r"\bend of (?:the )?(?:semester|term)\b")),
)
# A break the question names: its entries are the ones whose titles name it.
_BREAK_NAMES = ("thanksgiving", "spring", "winter", "fall", "summer")
_TERM = re.compile(r"\b(winter|spring|summer|fall|autumn)(?:\s+(?:semester|term|session))?"
                   r"(?:\s+(20\d{2}))?\b")
_NEXT = re.compile(r"\bnext (?:semester|term)\b")
# Words of a plain calendar question besides its kind and term words; any other word means it
# asks more ("if I'm a transfer student"), and the model reads it.
_PLAIN = frozenset("""
when is are was the a an what whats which day date dates last first of to for do does did i we
my this next current semester term session year classes class course courses start starts begin
begins end ends finish finishes over break finals final exam exams examinations week ramapo
college at in on deadline deadlines due grades grade add drop withdraw withdrawal withdrawing from
a refund refunds tuition thanksgiving spring winter summer fall autumn recess
""".split())


def kind_asked(message: str) -> str | None:
    """The one kind of calendar date a question asks when it is, or None."""
    text = message.casefold().replace("’", "'")
    if not re.search(r"\bwhen\b|\bwhat (?:day|date)\b|\blast day\b|\bdeadline\b", text):
        return None
    found = [kind for kind, pattern in _KINDS if pattern.search(text)]
    words = re.findall(r"[a-z0-9]+", text.replace("'", ""))
    if len(found) != 1 or any(w not in _PLAIN and not re.fullmatch(r"20\d{2}", w)
                              for w in words):
        return None
    return found[0]


def kind_named(message: str) -> str | None:
    """The one kind of calendar date a question names when it asks when or a deadline, whatever
    else it asks ("what's the deadline to withdraw and what shows on my transcript"): code says
    those dates first and the model the rest (11-03: the reply gave a page's 7-week dates and left
    out the calendar's full-semester Nov 6 deadline). None for no kind or two."""
    text = message.casefold().replace("’", "'")
    if not re.search(r"\bwhen\b|\bwhat (?:day|date)\b|\blast day\b|\bdeadlines?\b|"
                     r"\bdue date\b|\bby when\b|\bhow late\b|\buntil when\b", text):
        return None
    found = [kind for kind, pattern in _KINDS if pattern.search(text)]
    return found[0] if len(found) == 1 else None


def term_spans(dates: Sequence[dict[str, Any]]) -> dict[str, tuple[date, date]]:
    """Each term's first and last listed day, by the term names its dates carry."""
    spans: dict[str, tuple[date, date]] = {}
    for entry in dates:
        try:
            day = date.fromisoformat(str(entry.get("date")))
        except ValueError:
            continue
        for name in entry.get("terms") or []:
            first, last = spans.get(str(name), (day, day))
            spans[str(name)] = (min(first, day), max(last, day))
    return spans


def term_asked(message: str, terms: Sequence[dict[str, Any]],
               spans: dict[str, tuple[date, date]], today: date) -> dict[str, Any] | None:
    """The term a question asks about: one it names ("spring", "fall 2026"; a season alone is its
    term in session or the next to start), the fall or spring after the current one ("next
    semester"), else the fall or spring in session today, else the next term to start."""
    text = message.casefold()
    dated = sorted((t for t in terms if t.get("name") in spans),
                   key=lambda t: spans[str(t["name"])][0])

    def on(term: dict[str, Any]) -> bool:
        first, last = spans[str(term["name"])]
        return first <= today <= last
    current = (next((t for t in dated if on(t) and t.get("season") in ("fall", "spring")), None)
               or next((t for t in dated if on(t)), None))
    named = _TERM.search(text)
    if named:
        season = "fall" if named.group(1) == "autumn" else named.group(1)
        matches = [t for t in dated if t.get("season") == season
                   and (not named.group(2) or t.get("year") == int(named.group(2)))]
        if not named.group(2):
            matches = [t for t in matches if spans[str(t["name"])][1] >= today]
        return matches[0] if matches else None
    if _NEXT.search(text):
        if current is None:
            return None
        later = [t for t in dated if spans[str(t["name"])][0] > spans[str(current["name"])][1]
                 and t.get("season") in ("fall", "spring")]
        return later[0] if later else None
    return current or next((t for t in dated if spans[str(t["name"])][0] >= today), None)


def entries_asked(message: str, kind: str, term: dict[str, Any],
                  dates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The term's entries of the kind asked, in date order; for a break the question names
    ("Thanksgiving break"), the break and holiday entries whose titles name it."""
    listed = [e for e in dates if term.get("name") in (e.get("terms") or [])]
    if kind == "break":
        text = message.casefold()
        named = [w for w in _BREAK_NAMES if re.search(rf"\b{w}\b", text)
                 and not re.search(rf"\b{w}\s+(?:semester|term|20\d\d)\b", text)]
        found = [e for e in listed if e.get("kind") in ("break", "holiday")
                 and (not named or any(w in str(e.get("title")).casefold() for w in named))]
    else:
        found = [e for e in listed if e.get("kind") == kind]
    return sorted(found, key=lambda e: (str(e.get("date")), str(e.get("title"))))


def _day(value: str) -> str:
    day = date.fromisoformat(value)
    return f"{day:%A, %B} {day.day}, {day.year}"


# An entry's time as the calendar publishes it: a whole day ("12:00 am - 11:59 pm") says nothing
# its day does not, and a span of days ("December 09, 12:00 am -- December 15, 11:59 pm") is
# said by its last day.
_WHOLE_DAY = re.compile(r"^12:00 am(?:\s*-\s*11:59 pm)?$", re.I)
_DAY_SPAN = re.compile(r"^\w+ \d{1,2}, 12:00 am\s*--\s*(\w+) 0?(\d{1,2}), 11:59 pm$", re.I)


def _time(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text or _WHOLE_DAY.match(text):
        return ""
    if span := _DAY_SPAN.match(text):
        return f" (through {span.group(1)} {span.group(2)})"
    return f" ({literal(text)})"


def calendar_text(term: dict[str, Any], entries: Sequence[dict[str, Any]],
                  n: int = 1) -> str | None:
    """Code's answer: the entries asked, each with its day, title and time as the term's academic
    calendar lists them [n]. None when there are none (the model reads the pages then)."""
    if not entries:
        return None
    lines = [f"- {_day(str(e['date']))}: {literal(e['title'])}{_time(e.get('time'))}"
             for e in entries]
    return "\n".join([f"The {literal(term['name'])} academic calendar lists [{n}]:", *lines])
