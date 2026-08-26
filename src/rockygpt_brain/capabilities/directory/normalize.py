"""Between public directory fields and the campus contact search."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.narrow import holds
from rockygpt_brain.capabilities.types import Reader


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


FIELDS: dict[str, Reader] = {
    "name": lambda r: _text(r, "name"),
    "department": lambda r: _text(r, "department"),
    "phone": lambda r: _text(r, "phone"),
    "email": lambda r: _text(r, "email"),
    "office": lambda r: _text(r, "office"),
}

SORT: dict[str, Reader] = {
    "name": lambda r: _text(r, "name").casefold(),
    "department": lambda r: _text(r, "department").casefold(),
    "office": lambda r: _text(r, "office").casefold(),
}


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    terms = [filters[name] for name in ("name", "department") if name in filters]
    return {"q": " ".join(terms), "at": now.isoformat()}


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    for name in ("name", "department"):
        wanted = filters.get(name)
        if wanted and not holds(_text(record, name), wanted):
            return False
    return True
