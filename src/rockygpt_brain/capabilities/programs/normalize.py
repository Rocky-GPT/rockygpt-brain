"""Between program-plan fields and the academic-program search."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.narrow import holds
from rockygpt_brain.capabilities.types import Reader

_GRADUATE = re.compile(
    r"\b(?:graduate|master|doctor|doctoral|mba|mpp|msn|msw|mfa|dnp|m\.a\.|m\.s\.)\b",
    re.IGNORECASE,
)
_UNDERGRADUATE = re.compile(r"\b(?:undergraduate|bachelor|b\.a\.|b\.s\.)\b", re.IGNORECASE)


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


def _level(record: dict[str, Any]) -> str:
    explicit = _text(record, "level").strip().casefold()
    if explicit in {"graduate", "undergraduate"}:
        return explicit
    words = " ".join((_text(record, "degree"), _text(record, "name")))
    if _GRADUATE.search(words):
        return "graduate"
    if _UNDERGRADUATE.search(words):
        return "undergraduate"
    kind = _text(record, "programKind").casefold()
    return "undergraduate" if kind in {"major", "minor", "undeclared"} else ""


FIELDS: dict[str, Reader] = {
    "name": lambda r: _text(r, "name"),
    "degree": lambda r: _text(r, "degree"),
    "programKind": lambda r: _text(r, "programKind"),
    "level": _level,
    "school": lambda r: _text(r, "school"),
    "description": lambda r: _text(r, "description"),
    "programUrl": lambda r: _text(r, "programUrl"),
}

SORT: dict[str, Reader] = {
    "name": lambda r: _text(r, "name").casefold(),
    "degree": lambda r: _text(r, "degree").casefold(),
    "programKind": lambda r: _text(r, "programKind").casefold(),
    "level": _level,
    "school": lambda r: _text(r, "school").casefold(),
}


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    terms = [
        filters[name]
        for name in ("name", "subject", "programKind", "degree", "school", "level")
        if name in filters
    ]
    return {"q": " ".join(terms), "at": now.isoformat()}


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    if wanted := filters.get("name"):
        if not holds(_text(record, "name"), wanted):
            return False
    if wanted := filters.get("subject"):
        searchable = " ".join((_text(record, "name"), _text(record, "description")))
        if not holds(searchable, wanted):
            return False
    if wanted := filters.get("programKind"):
        if _text(record, "programKind").casefold() != wanted.casefold():
            return False
    for name in ("degree", "school"):
        wanted = filters.get(name)
        if wanted and not holds(_text(record, name), wanted):
            return False
    if wanted := filters.get("level"):
        if _level(record) != wanted.casefold():
            return False
    return True
