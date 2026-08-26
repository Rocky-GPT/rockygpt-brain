"""Between hours-plan fields and the two structured hours searches."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any

from rockygpt_brain.capabilities.narrow import holds

_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AaPp])")


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


def _minutes(record: dict[str, Any], field: str) -> int:
    match = _CLOCK.match(_text(record, field).strip())
    if not match:
        return 0
    hour = int(match.group(1)) % 12
    minute = int(match.group(2) or 0)
    return (hour + (12 if match.group(3).upper() == "P" else 0)) * 60 + minute


FIELDS = {
    "name": lambda r: _text(r, "name"),
    "kind": lambda r: _text(r, "kind"),
    "day": lambda r: _text(r, "day"),
    "schedule": lambda r: _text(r, "schedule"),
    "openNow": lambda r: r.get("openNow") if isinstance(r.get("openNow"), bool) else None,
    "opensAt": lambda r: _text(r, "opensAt"),
    "closesAt": lambda r: _text(r, "closesAt"),
}

SORT = {
    "name": lambda r: _text(r, "name").casefold(),
    "kind": lambda r: _text(r, "kind").casefold(),
    "day": lambda r: _text(r, "day").casefold(),
    "opensAt": lambda r: _minutes(r, "opensAt"),
    "closesAt": lambda r: _minutes(r, "closesAt"),
    "openNow": lambda r: r.get("openNow") is True,
}


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _day(filters: dict[str, str], now: datetime) -> str:
    raw = filters.get("day", "").strip()
    if raw:
        if parsed := _date(raw):
            return parsed.strftime("%A")
        return raw.title()
    if raw_date := filters.get("date"):
        if parsed := _date(raw_date):
            return parsed.strftime("%A")
    return now.strftime("%A")


def _at(filters: dict[str, str], now: datetime) -> str:
    if value := filters.get("openAt"):
        return value
    if value := filters.get("date"):
        parsed = _date(value)
        if parsed is not None and parsed != now.date():
            return datetime.combine(parsed, time(hour=12), tzinfo=now.tzinfo).isoformat()
    return now.isoformat()


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    return {"q": filters.get("name", ""), "day": _day(filters, now), "at": _at(filters, now)}


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    if name := filters.get("name"):
        if not holds(_text(record, "name"), name):
            return False
    # With no named venue, `openAt` means "which places are open?" A named
    # venue keeps its row even when closed, because that negative answer is the
    # useful result rather than an unexplained empty match.
    if filters.get("openAt") and not filters.get("name"):
        return record.get("openNow") is True
    return True
