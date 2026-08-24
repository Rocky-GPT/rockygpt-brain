"""Deterministic selection over authoritative records. Contract section 6.2.

The Worker is allowed to order and select. What it is not allowed to do is
select from a set it cannot see all of: an extremum computed over a truncated
result is a guess wearing the shape of an answer.

Nothing in this module knows what a record means. It reads the field named by
the capability's ordering declaration and applies the direction the relation
implies, so a new domain is a declaration rather than a branch.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from rockygpt_brain.core.capabilities import Ordering
from rockygpt_brain.core.interpretation import Relation

Direction = Literal["ascending", "descending"]

#: Which end of the declared order each extremal relation asks for.
EXTREMAL_DIRECTION: dict[Relation, Direction] = {
    Relation.EARLIEST: "ascending",
    Relation.NEXT: "ascending",
    Relation.LATEST: "descending",
}


def is_complete(completeness: Any) -> bool:
    """Whether a result set can support a relation defined over all of it.

    Requires the source to say so positively. An absent or unreadable
    completeness block is treated as incomplete, because the guarantee an
    extremum needs is exactly the one that is then missing.
    """

    if not isinstance(completeness, dict):
        return False
    if completeness.get("truncated") is not False:
        return False
    if completeness.get("state") not in (None, "complete"):
        return False
    matched = completeness.get("matched")
    returned = completeness.get("returned")
    if isinstance(matched, int) and isinstance(returned, int) and matched != returned:
        return False
    return True


def field_value(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def sort_key(value: Any, kind: str) -> tuple[int, float | str]:
    """Order a declared field. Unreadable values sort last in either direction."""

    if value is None:
        return (1, "")
    if kind == "number" and isinstance(value, (int, float)):
        return (0, float(value))
    text = str(value).strip()
    if kind == "time":
        for pattern in ("%I:%M %p", "%H:%M", "%I:%M%p"):
            try:
                parsed = datetime.strptime(text, pattern)
            except ValueError:
                continue
            return (0, float(parsed.hour * 60 + parsed.minute))
        return (1, text.casefold())
    if kind == "date":
        try:
            return (0, datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return (1, text.casefold())
    return (0, text.casefold())


def select_extremal(
    records: list[dict[str, Any]],
    ordering: Ordering,
    direction: Direction,
) -> list[dict[str, Any]]:
    """Order by the declared field and take one end. Callers verify completeness."""

    readable = [r for r in records if field_value(r, ordering.field) is not None]
    if not readable:
        return []
    ordered = sorted(
        readable,
        key=lambda record: sort_key(field_value(record, ordering.field), ordering.kind),
        reverse=direction == "descending",
    )
    return ordered[:1]
