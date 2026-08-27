from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.entities import (
    EntityCandidate,
    EntityNotFound,
    resolve_entity,
)
from rockygpt_brain.capabilities.narrow import holds
from rockygpt_brain.services.data import DataPort

_SUBJECT_CODE = re.compile(r"[A-Za-z]{2,6}")


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


async def resolve_filters(
    filters: dict[str, str], now: datetime, data: DataPort
) -> dict[str, str]:
    """Turn a subject mention into the catalogue's own code.

    The catalogue names two thirds of the codes courses actually use; the rest
    are language and interprofessional prefixes upstream files under no
    department. A mention that matches nothing but is shaped like a code is
    kept as one, so `JAPN` still narrows while `computer science` resolves
    through the name. An ambiguous mention is never resolved this way: two
    subjects with an equal claim is a question to ask, not a coin to toss.
    """
    resolved = dict(filters)
    mention = resolved.pop("subject", "").strip()
    if not mention:
        return resolved
    records = await data.course_subjects({"at": now.isoformat()})
    candidates = [
        EntityCandidate(
            _text(record, "code"),
            _text(record, "name"),
            tuple(item for item in record.get("aliases", []) if isinstance(item, str)),
        )
        for record in records
        if _text(record, "code")
    ]
    try:
        resolved["subjectCode"] = resolve_entity("course_subject", mention, candidates)
    except EntityNotFound:
        if not _SUBJECT_CODE.fullmatch(mention):
            raise
        resolved["subjectCode"] = mention.upper()
    return resolved


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    terms = [
        filters[name] for name in ("code", "subjectCode", "name", "attribute") if name in filters
    ]
    return {"q": " ".join(terms), "at": now.isoformat()}


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    if code := filters.get("code"):
        if _compact(_text(record, "code")) != _compact(code):
            return False
    if name := filters.get("name"):
        if not holds(_text(record, "name"), name):
            return False
    if subject := filters.get("subjectCode"):
        prefix = re.match(r"[A-Za-z]+", _text(record, "code"))
        if not prefix or prefix.group().casefold() != subject.casefold():
            return False
    if attribute := filters.get("attribute"):
        if not any(attribute.casefold() in item.casefold() for item in _attributes(record)):
            return False
    return True
