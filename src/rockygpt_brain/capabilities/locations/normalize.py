"""Between location-plan fields and the campus map resolver."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.types import Reader


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


def _strings(record: dict[str, Any], name: str) -> list[str]:
    value = record.get(name)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


FIELDS: dict[str, Reader] = {
    "key": lambda r: _text(r, "key"),
    "name": lambda r: _text(r, "name"),
    "type": lambda r: _text(r, "type"),
    "mapUrl": lambda r: _text(r, "mapUrl"),
    "aliases": lambda r: _strings(r, "aliases"),
    "buildingName": lambda r: _text(r, "buildingName"),
    "room": lambda r: _text(r, "room"),
    "category": lambda r: _text(r, "category"),
    "description": lambda r: _text(r, "description"),
    "officeUrl": lambda r: _text(r, "officeUrl"),
}

SORT: dict[str, Reader] = {
    "name": lambda r: _text(r, "name").casefold(),
    "type": lambda r: _text(r, "type").casefold(),
    "buildingName": lambda r: _text(r, "buildingName").casefold(),
    "room": lambda r: _text(r, "room").casefold(),
    "category": lambda r: _text(r, "category").casefold(),
}


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    del now  # The campus map is not time-dependent.
    terms = [filters[name] for name in ("name", "building", "room") if name in filters]
    return {"q": " ".join(terms)}


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    if wanted := filters.get("name"):
        names = [_text(record, "name"), *_strings(record, "aliases")]
        if not any(wanted.casefold() in name.casefold() for name in names):
            return False
    if wanted := filters.get("type"):
        if _text(record, "type").casefold() != wanted.casefold():
            return False
    if wanted := filters.get("building"):
        buildings = (_text(record, "buildingName"), _text(record, "name"))
        if not any(wanted.casefold() in building.casefold() for building in buildings):
            return False
    if wanted := filters.get("room"):
        if wanted.casefold() not in _text(record, "room").casefold():
            return False
    return True
