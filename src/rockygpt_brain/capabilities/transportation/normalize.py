"""Between transportation-plan fields and the typed shuttle service."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.types import Reader

_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})\s*([AaPp])")
_FETCH_LIMIT = 100


def minutes(value: str) -> int:
    """Return a display clock as minutes past midnight for chronological sorting."""
    match = _CLOCK.match(value.strip())
    if not match:
        return 0
    hour, minute, half = int(match.group(1)) % 12, int(match.group(2)), match.group(3).upper()
    return (hour + (12 if half == "P" else 0)) * 60 + minute


FIELDS: dict[str, Reader] = {
    "departureTime": lambda r: r.get("departure", {}).get("time", ""),
    "arrivalTime": lambda r: r.get("arrival", {}).get("time", ""),
    "route": lambda r: r.get("route", ""),
    "origin": lambda r: r.get("matchedOrigin", {}).get("location", ""),
    "destination": lambda r: r.get("matchedDestination", {}).get("location", ""),
}

SORT: dict[str, Reader] = {
    "departureTime": lambda r: minutes(FIELDS["departureTime"](r)),
    "arrivalTime": lambda r: minutes(FIELDS["arrivalTime"](r)),
}


def query(filters: dict[str, str], now: datetime) -> dict[str, Any]:
    """Translate public transportation filters to the typed shuttle request."""
    after = filters.get("departingAfter")
    request: dict[str, Any] = {
        "selection": "all",
        "timeScope": "remaining" if after else "full_day",
        "asOf": after or now.isoformat(),
        "limit": _FETCH_LIMIT,
    }
    if "date" in filters:
        request["serviceDate"] = filters["date"]
    for name in ("route", "origin", "destination"):
        if name in filters:
            request[name] = filters[name]
    return request
