from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from rockygpt_brain.capabilities.narrow import holds
from rockygpt_brain.capabilities.types import Reader

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_DAY = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(\d{1,2})\b",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(20\d{2})\b")
_TOPIC_WORD = re.compile(r"[a-z0-9]+")
_TOPIC_NOISE = frozenset(
    {"academic", "calendar", "date", "dates", "deadline", "deadlines"}
)
_REGISTRATION = frozenset(
    {"enroll", "enrollment", "enrol", "enrolment", "reg", "register", "registration"}
)
_DEADLINE = frozenset({"deadline", "deadlines", "last"})
_TOPIC_ALIASES = {"withdrawal": "withdraw"}


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


def _record_date(record: dict[str, Any]) -> date | None:
    instant = _text(record, "startsAt").strip()
    if instant:
        try:
            return datetime.fromisoformat(instant.replace("Z", "+00:00")).date()
        except ValueError:
            pass

    label = _text(record, "date").strip()
    try:
        return date.fromisoformat(label)
    except ValueError:
        pass

    day_match = _MONTH_DAY.search(label)
    year_match = _YEAR.search(_text(record, "term")) or _YEAR.search(label)
    if not day_match or not year_match:
        return None
    month = _MONTHS[day_match.group(1).casefold()]
    try:
        return date(int(year_match.group(1)), month, int(day_match.group(2)))
    except ValueError:
        return None


def _starts_at(record: dict[str, Any]) -> str:
    explicit = _text(record, "startsAt")
    if explicit:
        return explicit
    parsed = _record_date(record)
    return parsed.isoformat() if parsed else ""


def _date_sort(record: dict[str, Any]) -> tuple[int, str]:
    parsed = _record_date(record)
    return (parsed.toordinal() if parsed else 0, _text(record, "title").casefold())


def _topic_terms(topic: str) -> tuple[str, ...]:
    """Translate student wording to the vocabulary the calendar publishes.

    Registration cutoffs are published as "Last Day to Add/Drop", not as a
    "registration deadline". Keeping the alias here makes the same terms drive
    both the DATA query and the fail-closed local match.
    """
    words = _TOPIC_WORD.findall(topic.casefold())
    vocabulary = set(words)
    if vocabulary & _REGISTRATION and vocabulary & _DEADLINE:
        return ("add", "drop")

    terms = tuple(
        _TOPIC_ALIASES.get(word, word) for word in words if word not in _TOPIC_NOISE
    )
    if terms:
        return terms
    if vocabulary & _DEADLINE:
        return ("last", "day")
    return ()


FIELDS: dict[str, Reader] = {
    "term": lambda r: _text(r, "term"),
    "date": lambda r: _text(r, "date"),
    "startsAt": _starts_at,
    "title": lambda r: _text(r, "title"),
    "description": lambda r: _text(r, "description"),
}

SORT: dict[str, Reader] = {
    "term": lambda r: _text(r, "term").casefold(),
    "date": _date_sort,
    "startsAt": _date_sort,
    "title": lambda r: _text(r, "title").casefold(),
}


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    terms: list[str] = []
    if topic := filters.get("topic"):
        terms.extend(_topic_terms(topic))
    terms.extend(filters[name] for name in ("title", "term") if name in filters)
    return {"q": " ".join(terms), "at": now.isoformat()}


def _wanted_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    if topic := filters.get("topic"):
        searchable = " ".join(
            (_text(record, "term"), _text(record, "title"), _text(record, "description"))
        )
        if not holds(searchable, " ".join(_topic_terms(topic))):
            return False
    for name in ("title", "term"):
        wanted = filters.get(name)
        if wanted and not holds(_text(record, name), wanted):
            return False
    if wanted := filters.get("date"):
        parsed = _wanted_date(wanted)
        if parsed is not None:
            if _record_date(record) != parsed:
                return False
        elif wanted.casefold() not in _text(record, "date").casefold():
            return False
    if after := filters.get("startsAfter"):
        try:
            threshold = datetime.fromisoformat(after.replace("Z", "+00:00")).date()
        except ValueError:
            return False
        record_date = _record_date(record)
        if record_date is None or record_date < threshold:
            return False
    return True
