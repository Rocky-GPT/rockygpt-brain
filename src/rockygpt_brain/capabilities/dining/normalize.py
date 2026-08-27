from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


def _contains(actual: str, wanted: str) -> bool:
    return wanted.casefold() in actual.casefold()


def _calories(record: dict[str, Any]) -> int:
    match = re.search(r"\d+", _text(record, "calories"))
    return int(match.group()) if match else 0


FIELDS = {
    "date": lambda r: _text(r, "date"),
    "name": lambda r: _text(r, "name"),
    "meal": lambda r: _text(r, "meal"),
    "station": lambda r: _text(r, "station"),
    "calories": lambda r: _text(r, "calories"),
    "vegan": lambda r: r.get("vegan") is True,
    "vegetarian": lambda r: r.get("vegetarian") is True,
    "allergens": lambda r: r.get("allergens") if isinstance(r.get("allergens"), list) else [],
}

SORT = {
    "date": lambda r: _text(r, "date"),
    "name": lambda r: _text(r, "name").casefold(),
    "meal": lambda r: _text(r, "meal").casefold(),
    "station": lambda r: _text(r, "station").casefold(),
    "calories": _calories,
    "vegan": lambda r: r.get("vegan") is True,
    "vegetarian": lambda r: r.get("vegetarian") is True,
}


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    terms = [filters[name] for name in ("name", "station", "dietary") if name in filters]
    out = {"q": " ".join(terms), "at": now.isoformat()}
    if meal := filters.get("meal"):
        out["meal"] = meal.upper()
    return out


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    if date := filters.get("date"):
        record_date = _text(record, "date")
        if record_date and record_date != date:
            return False
    if meal := filters.get("meal"):
        if _text(record, "meal").casefold() != meal.casefold():
            return False
    if name := filters.get("name"):
        if not _contains(_text(record, "name"), name):
            return False
    if station := filters.get("station"):
        if not _contains(_text(record, "station"), station):
            return False
    if dietary := filters.get("dietary"):
        choice = dietary.strip().casefold()
        if choice == "vegan":
            return record.get("vegan") is True
        if choice == "vegetarian":
            return record.get("vegetarian") is True
        return False
    return True
