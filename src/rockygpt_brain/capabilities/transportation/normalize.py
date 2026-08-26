from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.types import Reader

_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})\s*([AaPp])")
_TIME_OF_DAY = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(?:([AaPp])M?)?$")
_FETCH_LIMIT = 100


def minutes(value: str) -> int:
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


def instant(value: str, now: datetime) -> str:
    match = _TIME_OF_DAY.match(value.strip().replace(".", "").upper())
    if not match:
        return value
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if half:
        hour = hour % 12 + (12 if half.upper() == "P" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return value
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def query(filters: dict[str, str], now: datetime) -> dict[str, Any]:
    after = filters.get("departingAfter")
    request: dict[str, Any] = {
        "selection": "all",
        "timeScope": "remaining" if after else "full_day",
        "asOf": instant(after, now) if after else now.isoformat(),
        "limit": _FETCH_LIMIT,
    }
    if "date" in filters:
        request["serviceDate"] = filters["date"]
    for name in ("route", "origin", "destination"):
        if name in filters:
            request[name] = filters[name]
    return request
