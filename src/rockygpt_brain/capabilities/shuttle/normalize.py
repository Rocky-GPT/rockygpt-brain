"""Between the plan's vocabulary and the data service's.

Two translations, and they run in opposite directions. `query` turns the
filters a plan named into the request the service expects. `FIELDS` turns one
record it returned back into the field names the capability publishes.

Neither vocabulary leaks past this file. The service has its own selection
words — `first`, `next`, `current` — and nothing here uses them: the request
asks for everything and the plan's operation decides what survives. That is
what keeps "the next shuttle" from becoming a concept a plan has to know.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})\s*([AaPp])")

#: Enough rows that the operation decides the answer, not the fetch — and no
#: more than the data service will accept. It caps `limit` at 100 and rejects
#: the whole request above that, so asking for 200 did not return 100 rows, it
#: returned a 400 and failed every CODE turn.
_FETCH_LIMIT = 100

Reader = Callable[[dict[str, Any]], Any]


def minutes(value: str) -> int:
    """`7:05 PM` as minutes past midnight, so times sort as times."""
    match = _CLOCK.match(value.strip())
    if not match:
        return 0
    hour, minute, half = int(match.group(1)) % 12, int(match.group(2)), match.group(3).upper()
    return (hour + (12 if half == "P" else 0)) * 60 + minute


#: How each published field is read out of one record the service returned.
FIELDS: dict[str, Reader] = {
    "departureTime": lambda r: r.get("departure", {}).get("time", ""),
    "arrivalTime": lambda r: r.get("arrival", {}).get("time", ""),
    "route": lambda r: r.get("route", ""),
    "origin": lambda r: r.get("matchedOrigin", {}).get("location", ""),
    "destination": lambda r: r.get("matchedDestination", {}).get("location", ""),
}

#: Where sorting on the published value would sort wrongly. `7:05 PM` before
#: `10:00 AM` as text; the other way round as time.
SORT: dict[str, Reader] = {
    "departureTime": lambda r: minutes(FIELDS["departureTime"](r)),
    "arrivalTime": lambda r: minutes(FIELDS["arrivalTime"](r)),
}


def query(filters: dict[str, str], now: datetime) -> dict[str, Any]:
    """The filters a plan named, as the request the data service takes."""
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
