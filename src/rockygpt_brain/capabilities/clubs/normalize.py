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
    "category": lambda r: _text(r, "category"),
    "websiteUrl": lambda r: _text(r, "websiteUrl"),
}

SORT: dict[str, Reader] = {
    "name": lambda r: _text(r, "name").casefold(),
    "category": lambda r: _text(r, "category").casefold(),
}


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    terms = [filters[name] for name in ("name", "category") if name in filters]
    return {"q": " ".join(terms), "at": now.isoformat()}


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    for name in ("name", "category"):
        wanted = filters.get(name)
        if wanted and not holds(_text(record, name), wanted):
            return False
    return True
