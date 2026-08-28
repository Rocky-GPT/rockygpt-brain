from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from rockygpt_brain.capabilities.entities import EntityCandidate, resolve_entity
from rockygpt_brain.capabilities.types import Reader
from rockygpt_brain.services.data import DataPort

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


def _text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    return value if isinstance(value, str) else ""


def record_date(record: dict[str, Any]) -> date | None:
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
    parsed = record_date(record)
    return parsed.isoformat() if parsed else ""


def _date_sort(record: dict[str, Any]) -> tuple[int, str]:
    parsed = record_date(record)
    return (parsed.toordinal() if parsed else 0, _text(record, "title").casefold())


FIELDS: dict[str, Reader] = {
    "family": lambda r: _text(r, "family"),
    "kind": lambda r: _text(r, "kind"),
    "term": lambda r: _text(r, "term"),
    "termId": lambda r: _text(r, "termId"),
    "session": lambda r: _text(r, "session"),
    "sessionId": lambda r: _text(r, "sessionId"),
    "date": lambda r: _text(r, "date"),
    "startsAt": _starts_at,
    "title": lambda r: _text(r, "title"),
    "description": lambda r: _text(r, "description"),
}

SORT: dict[str, Reader] = {
    "family": lambda r: _text(r, "family").casefold(),
    "kind": lambda r: _text(r, "kind").casefold(),
    "term": lambda r: _text(r, "term").casefold(),
    "termId": lambda r: _text(r, "termId"),
    "session": lambda r: _text(r, "session").casefold(),
    "sessionId": lambda r: _text(r, "sessionId"),
    "date": _date_sort,
    "startsAt": _date_sort,
    "title": lambda r: _text(r, "title").casefold(),
}


async def resolve_filters(filters: dict[str, str], now: datetime, data: DataPort) -> dict[str, str]:
    """Resolve planner-facing entity mentions into calendar dataset identity."""
    resolved = dict(filters)

    # Every kind belongs to exactly one family, so a plan carrying both has
    # either said the same thing twice or picked a subtype the question did
    # not. Asked for the last day to register it answered `registration` and
    # then guessed `add_drop_deadline` beside it, which reads as precision and
    # is what drops the independent-study deadline from the same term. The
    # broad concept is the one the question named; the subtype beside it goes.
    if resolved.get("family") and resolved.get("kind"):
        resolved.pop("kind")

    term = resolved.pop("term", None)
    session = resolved.pop("session", None)
    if term or session:
        records = await data.calendar({"at": now.isoformat()})
        if term and term.casefold() not in {"current", "upcoming"}:
            terms = {(_text(record, "termId"), _text(record, "term")) for record in records}
            resolved["termId"] = resolve_entity(
                "academic_term",
                term,
                [EntityCandidate(id, label) for id, label in terms if id and label],
            )
        if session:
            sessions = {
                (_text(record, "sessionId"), _text(record, "session")) for record in records
            }
            aliases = {
                "session-i": ("session 1",),
                "session-ii": ("session 2",),
                "session-iii": ("session 3",),
                "session-iv": ("session 4",),
                "mini-session-i": ("mini 1", "mini i"),
                "mini-session-ii": ("mini 2", "mini ii"),
                "full-semester": ("full", "full session", "full summer"),
            }
            resolved["sessionId"] = resolve_entity(
                "academic_session",
                session,
                [
                    EntityCandidate(id, label, aliases.get(id, ()))
                    for id, label in sessions
                    if id and label
                ],
            )

    deadline_kinds = {
        "add_drop_deadline",
        "application_deadline",
        "grading_option_deadline",
        "independent_study_registration_deadline",
        "tuition_refund_deadline",
        "withdrawal_deadline",
    }
    if (
        (resolved.get("kind") in deadline_kinds or resolved.get("family") == "registration")
        and "date" not in resolved
        and "startsAfter" not in resolved
        and "termId" not in resolved
    ):
        resolved["startsAfter"] = now.isoformat()
    return resolved


def query(filters: dict[str, str], now: datetime) -> dict[str, str]:
    out = {"at": now.isoformat()}
    for name in (
        "family",
        "kind",
        "termId",
        "sessionId",
        "date",
        "startsAfter",
        "startsBefore",
    ):
        if value := filters.get(name):
            out[name] = value
    return out


def _wanted_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def matches(record: dict[str, Any], filters: dict[str, str]) -> bool:
    for name in ("family", "kind", "termId", "sessionId"):
        wanted = filters.get(name)
        if wanted and _text(record, name).casefold() != wanted.casefold():
            return False
    if wanted := filters.get("date"):
        parsed = _wanted_date(wanted)
        if parsed is not None:
            if record_date(record) != parsed:
                return False
        elif wanted.casefold() not in _text(record, "date").casefold():
            return False
    if after := filters.get("startsAfter"):
        try:
            threshold = datetime.fromisoformat(after.replace("Z", "+00:00")).date()
        except ValueError:
            return False
        actual_date = record_date(record)
        if actual_date is None or actual_date < threshold:
            return False
    if before := filters.get("startsBefore"):
        try:
            threshold = datetime.fromisoformat(before.replace("Z", "+00:00")).date()
        except ValueError:
            return False
        actual_date = record_date(record)
        if actual_date is None or actual_date >= threshold:
            return False
    return True
