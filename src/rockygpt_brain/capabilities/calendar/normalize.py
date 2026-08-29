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


_SEASONS = ("fall", "spring", "winter", "summer")

# Words that qualify a season without naming a different thing. "Fall break" is
# not the fall term, so the set is closed rather than "anything else".
_TERM_WORDS = frozenset({"semester", "term", "the", "this", "academic", "year"})


def _bare_season(mention: str) -> str | None:
    """The season a term mention names, when it names one and gives no year.

    A term mention carrying a year — `Fall 2026` — is an exact name and belongs
    to `resolve_entity`, which matches it against the dataset's own spelling. A
    mention with no year is not a weaker version of that; it is a different kind
    of thing. "The fall semester" names a season and leaves the year to the
    clock, exactly as "today" leaves the date to it.

    Returns None for anything else, so the strict resolver still sees every
    mention this cannot account for.
    """
    if _YEAR.search(mention):
        return None
    words: list[str] = re.findall(r"[a-z]+", mention.casefold())
    seasons = [word for word in words if word in _SEASONS]
    if len(seasons) != 1:
        return None
    if any(word not in _TERM_WORDS for word in words if word not in _SEASONS):
        return None
    return seasons[0]


def _term_for_season(season: str, records: list[dict[str, Any]], today: date) -> str | None:
    """The term of that season the college is in, or heading into.

    Resolved from the calendar's own dates rather than guessed: among the terms
    whose name begins with the season, the earliest one that has not finished.
    A season names a recurring thing, so an unqualified mention of it means the
    current instance and then the next — never the one three years out that
    happens to sort first, and never a coin toss between them.

    Falls back to the most recent past term when every instance has finished,
    which is the only remaining reading of "the fall semester" once no fall is
    still to come.
    """
    spans: dict[str, tuple[date, date]] = {}
    for record in records:
        term_id, label = _text(record, "termId"), _text(record, "term")
        if not term_id or not label.casefold().startswith(season):
            continue
        parsed = record_date(record)
        if parsed is None:
            continue
        low, high = spans.get(term_id, (parsed, parsed))
        spans[term_id] = (min(low, parsed), max(high, parsed))

    if not spans:
        return None
    unfinished = {name: span for name, span in spans.items() if span[1] >= today}
    if unfinished:
        # The one being lived through, then the one after it.
        return min(unfinished, key=lambda name: unfinished[name][0])
    # Every instance has finished, so the only remaining reading is the most
    # recent — not the earliest, which would name a term years gone.
    return max(spans, key=lambda name: spans[name][0])


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
            # A season with no year is resolved from the clock, not matched
            # against the dataset's names. `resolve_entity` is deliberately
            # strict — it refuses rather than picks the more popular reading —
            # and "fall" against `Fall 2026`, `Fall 2027` and `Fall 2028` is
            # exactly the coin toss it declines to make. It is not a coin toss
            # once the calendar's own dates are read: one of those three is the
            # fall the college is in.
            season = _bare_season(term)
            chosen = _term_for_season(season, records, now.date()) if season else None
            if chosen:
                resolved["termId"] = chosen
            else:
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
