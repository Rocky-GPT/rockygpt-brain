"""PYTHON: run the lane.

The stage between the two brains. A checked plan goes in; what the lane
produced comes out, and that is what BRAIN #2 writes the answer from.

Two jobs live here and nowhere else:

Translation. The plan is written in Rocky's own vocabulary — `date`,
`destination`, `orderBy` — and the data service has its own names for those.
Turning one into the other is this file's work, which is what keeps the
vocabulary free to stay small and generic.

The generic operations. `orderBy`, `limit`, `count` are applied here, in
Python, over whatever the lookup returned. The data service has its own
selection vocabulary; asking it for everything and sorting the result ourselves
means none of that vocabulary has to leak back into a plan.

A capability earns its executor with an entry in `_EXECUTORS`. Nothing else
moves.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rockygpt_brain.core.capabilities import CAPABILITIES
from rockygpt_brain.core.plan import Lane, Operation, Plan
from rockygpt_brain.core.validate import Rejected
from rockygpt_brain.services.data import DataPort, DataUnavailable

#: The most a lookup asks for before the operation narrows it down.
_FETCH_LIMIT = 100
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})\s*([AaPp])[Mm]?$")


@dataclass(frozen=True, slots=True)
class Execution:
    #: No lane here. The plan stage above already names it, and repeating it
    #: only invites the two to disagree. What this stage adds is whether the
    #: lane ran, and what came back.
    ran: bool
    note: str
    results: list[dict[str, Any]] = field(default_factory=list)
    count: int | None = None

    def summary(self) -> dict[str, Any]:
        """One of three shapes, and which one it is says what happened.

        ``{"note": ...}``      the lane did not run, and why
        ``{"count": n}``       it ran and counted
        ``{"results": [...]}`` it ran and listed — ``[]`` means it found none

        There is no ``ran`` flag because the shape is the flag. What a reader
        must never confuse is an empty ``results`` with a missing one: the
        first is "Rocky looked and there is nothing", the second is "Rocky
        never looked", and those are different answers. Keeping ``results``
        present-but-empty is what draws that line, so do not drop it when it
        is empty.
        """
        if not self.ran:
            return {"note": self.note}
        if self.count is not None:
            return {"count": self.count}
        return {"results": self.results}

    def grounding(self) -> list[dict[str, Any]] | None:
        """What BRAIN #2 answers from. None when nothing was looked up."""
        if not self.ran:
            return None
        if self.count is not None:
            return [{"count": self.count}]
        return self.results


Executor = Callable[[Plan, datetime, DataPort], Awaitable[list[dict[str, Any]]]]


def _minutes(value: str) -> int:
    """`7:05 PM` as minutes past midnight, so times sort as times."""
    match = _CLOCK.match(value.strip())
    if not match:
        return 0
    hour, minute, half = int(match.group(1)) % 12, int(match.group(2)), match.group(3).upper()
    return (hour + (12 if half == "P" else 0)) * 60 + minute


#: How each shuttle field is read out of one record the data service returned.
_SHUTTLE_FIELDS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "departureTime": lambda r: r.get("departure", {}).get("time", ""),
    "arrivalTime": lambda r: r.get("arrival", {}).get("time", ""),
    "route": lambda r: r.get("route", ""),
    "origin": lambda r: r.get("matchedOrigin", {}).get("location", ""),
    "destination": lambda r: r.get("matchedDestination", {}).get("location", ""),
}
_SHUTTLE_SORT: dict[str, Callable[[dict[str, Any]], Any]] = {
    "departureTime": lambda r: _minutes(_SHUTTLE_FIELDS["departureTime"](r)),
    "arrivalTime": lambda r: _minutes(_SHUTTLE_FIELDS["arrivalTime"](r)),
}


async def _shuttle(plan: Plan, now: datetime, data: DataPort) -> list[dict[str, Any]]:
    """A shuttle plan, in the shape the data service asks for."""
    values = plan.filter_values
    after = values.get("departingAfter")
    query: dict[str, Any] = {
        # Always "all": the plan's operation decides which trips survive, so the
        # data service is never asked to pick one.
        "selection": "all",
        "timeScope": "remaining" if after else "full_day",
        "asOf": after or now.isoformat(),
        "limit": _FETCH_LIMIT,
    }
    if "date" in values:
        query["serviceDate"] = values["date"]
    for ours, theirs in (("route", "route"), ("origin", "origin"), ("destination", "destination")):
        if ours in values:
            query[theirs] = values[ours]
    return await data.shuttle(query)


_EXECUTORS: dict[str, Executor] = {"shuttle": _shuttle}


def _project(record: dict[str, Any], capability: str) -> dict[str, Any]:
    """One record, cut down to the fields the capability publishes."""
    readers = _SHUTTLE_FIELDS if capability == "shuttle" else {}
    allowed = CAPABILITIES[capability].fields
    return {name: read(record) for name, read in readers.items() if name in allowed}


def _apply(
    records: list[dict[str, Any]], operation: Operation, capability: str
) -> tuple[list[dict[str, Any]], int | None]:
    """`orderBy`, `limit` and `count`, over whatever the lookup returned."""
    rows = list(records)
    sorters = _SHUTTLE_SORT if capability == "shuttle" else {}
    if operation.order_by:
        key = sorters.get(operation.order_by) or _SHUTTLE_FIELDS.get(operation.order_by)
        if key is not None:
            rows.sort(key=key, reverse=operation.direction == "descending")
    if operation.count:
        # Counted before the limit: the answer is how many matched, not how
        # many were kept.
        return [], len(rows)
    if operation.limit is not None:
        rows = rows[: operation.limit]
    return [_project(row, capability) for row in rows], None


async def run(checked: Plan | Rejected, now: datetime, data: DataPort) -> Execution:
    """Act on a checked plan."""
    if isinstance(checked, Rejected):
        return Execution(ran=False, note=checked.reason)

    capability = checked.capability or ""
    executor = _EXECUTORS.get(capability) if checked.lane is Lane.CODE else None
    if executor is None:
        missing = f"the {capability} capability" if capability else f"the {checked.lane.value} lane"
        return Execution(ran=False, note=f"no executor for {missing} yet")

    try:
        records = await executor(checked, now, data)
    except DataUnavailable as exc:
        return Execution(ran=False, note=f"the lookup did not happen: {exc}")

    results, count = _apply(records, checked.operation, capability)
    return Execution(ran=True, note="", results=results, count=count)
