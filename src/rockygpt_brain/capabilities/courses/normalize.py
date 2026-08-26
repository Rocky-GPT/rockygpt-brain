from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.narrow import holds


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


def _attributes(record: dict[str, Any]) -> list[str]:
    value = record.get("attributes")
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _code_sort(record: dict[str, Any]) -> tuple[str, int, str]:
    code = _text(record, "code").strip()
    match = re.match(r"([A-Za-z]+)\s*(\d+)(.*)", code)
    if not match:
        return (code.casefold(), 0, "")
    return (match.group(1).casefold(), int(match.group(2)), match.group(3).casefold())


def _credits(record: dict[str, Any]) -> float:
    match = re.search(r"\d+(?:\.\d+)?", _text(record, "credits"))
    return float(match.group()) if match else 0.0


FIELDS = {
    "code": lambda r: _text(r, "code"),
    "name": lambda r: _text(r, "name"),
    "description": lambda r: _text(r, "description"),
    "credits": lambda r: _text(r, "credits"),
    "attributes": _attributes,
    "courseUrl": lambda r: _text(r, "courseUrl"),
}

SORT = {
    "code": _code_sort,
    "name": lambda r: _text(r, "name").casefold(),
    "credits": _credits,
}


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    terms = [filters[name] for name in ("code", "subject", "name", "attribute") if name in filters]
    return {"q": " ".join(terms), "at": now.isoformat()}


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    if code := filters.get("code"):
        if _compact(_text(record, "code")) != _compact(code):
            return False
    if name := filters.get("name"):
        if not holds(_text(record, "name"), name):
            return False
    if subject := filters.get("subject"):
        subject_text = subject.casefold()
        code_prefix = re.match(r"[A-Za-z]+", _text(record, "code"))
        code_subject = code_prefix.group().casefold() if code_prefix else ""
        if subject_text != code_subject and subject_text not in _text(record, "name").casefold():
            return False
    if attribute := filters.get("attribute"):
        if not any(attribute.casefold() in item.casefold() for item in _attributes(record)):
            return False
    return True
