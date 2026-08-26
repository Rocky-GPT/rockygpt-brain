from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from rockygpt_brain.capabilities.narrow import holds

_DATE_FORMATS = ("%a, %b %d, %Y", "%b %d, %Y", "%Y-%m-%d")
_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AaPp])")


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


def _date(record: dict[str, Any]) -> date | None:
    value = _text(record, "date").strip()
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def _minutes(value: str) -> int:
    match = _CLOCK.match(value.strip())
    if not match:
        return 0
    hour = int(match.group(1)) % 12
    minute = int(match.group(2) or 0)
    return (hour + (12 if match.group(3).upper() == "P" else 0)) * 60 + minute


def _when(record: dict[str, Any]) -> tuple[int, int]:
    day = _date(record)
    return (day.toordinal() if day else 0, _minutes(_text(record, "startTime")))


FIELDS = {
    "title": lambda r: _text(r, "title"),
    "date": lambda r: _text(r, "date"),
    "startTime": lambda r: _text(r, "startTime"),
    "endTime": lambda r: _text(r, "endTime"),
    "organizer": lambda r: _text(r, "organizer"),
    "description": lambda r: _text(r, "description"),
    "eventUrl": lambda r: _text(r, "eventUrl"),
}

SORT = {
    "title": lambda r: _text(r, "title").casefold(),
    "date": _when,
    "startTime": _when,
    "endTime": lambda r: _minutes(_text(r, "endTime")),
    "organizer": lambda r: _text(r, "organizer").casefold(),
}


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    terms = [filters[name] for name in ("topic", "title", "organizer") if name in filters]
    return {
        "q": " ".join(terms),
        "at": filters.get("startsAfter", now.isoformat()),
    }


def matches(record: dict[str, Any], filters: dict[str, str], now: datetime) -> bool:
    if title := filters.get("title"):
        if not holds(_text(record, "title"), title):
            return False
    if organizer := filters.get("organizer"):
        if not holds(_text(record, "organizer"), organizer):
            return False
    if wanted := filters.get("date"):
        try:
            wanted_date = date.fromisoformat(wanted)
        except ValueError:
            return False
        if _date(record) != wanted_date:
            return False
    after = filters.get("startsAfter")
    if after or "date" not in filters:
        try:
            threshold = datetime.fromisoformat(after) if after else now
        except ValueError:
            return False
        day = _date(record)
        if day is None:
            return False
        event = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo).replace(
            hour=_minutes(_text(record, "startTime")) // 60,
            minute=_minutes(_text(record, "startTime")) % 60,
        )
        if event < threshold:
            return False
    return True
