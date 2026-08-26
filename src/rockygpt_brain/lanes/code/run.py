"""Run a capability, then apply the plan's operation to what came back.

The split is deliberate. The capability knows how to fetch and how to read its
own records; this knows `orderBy`, `limit` and `count`, which mean the same
thing whatever was looked up. Neither has to learn the other's job, and the
registry is what joins them.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from rockygpt_brain.brain.execute.schema import CAMPUS_DATA, Execution, present
from rockygpt_brain.brain.plan.schema import Operation, Plan
from rockygpt_brain.capabilities.registry import capability_for
from rockygpt_brain.errors import DatasetUnavailable, Unsupported
from rockygpt_brain.services.data import DataPort, DataUnavailable


class LaneFailed(Exception):
    """Why a lane produced nothing. The cause of the ServiceError."""


async def run(checked: Plan, now: datetime, data: DataPort) -> Execution:
    capability = checked.capability or ""
    entry = capability_for(capability)
    if entry is None:
        raise Unsupported("Rocky cannot look that up yet.") from LaneFailed(
            f"no capability named {capability!r}"
        )

    try:
        # The filters, not the plan. A capability has no business knowing what
        # a lane is or which operations exist.
        records = await entry.execute(checked.filter_values, now, data)
    except DataUnavailable as exc:
        raise DatasetUnavailable("Rocky could not reach campus data just now.") from exc

    return replace(
        apply(records, checked.operation, capability),
        looked_for={"capability": capability, "filters": checked.filter_values},
    )


def apply(records: list[dict[str, Any]], operation: Operation, capability: str) -> Execution:
    """`orderBy`, `limit` and `count`, then one page of what is left.

    Three cuts, and they are not the same kind of thing. `limit` is what the
    question asked for, so a result cut to it is the answer. The page is what a
    message holds, so a result cut to it is a page — and `found` is what says
    which of the two happened. Silently they look identical, which is how two
    hundred rows became "the courses Ramapo offers".

    The page is also why nothing here needs a cap of its own any more. The data
    service hands over its whole table now — the course catalogue is 3,344
    entries and three megabytes, and a question like "what courses does Ramapo
    offer" once put all of it in the prompt and failed the turn outright. It is
    a page of 25 that goes in the prompt, and 3,344 that gets reported.
    """
    rows = list(records)
    entry = capability_for(capability)
    if entry is None:
        raise Unsupported("Rocky cannot look that up yet.") from LaneFailed(
            f"no capability named {capability!r}"
        )
    if operation.order_by:
        key = entry.sort.get(operation.order_by) or entry.read.get(operation.order_by)
        if key is not None:
            rows.sort(key=key, reverse=operation.direction == "descending")
    if operation.count:
        # Counted before the limit: the answer is how many matched, not how
        # many were kept.
        return Execution(CAMPUS_DATA, count=len(rows))
    if operation.limit is not None:
        rows = rows[: operation.limit]
    shown = present(len(rows))
    page = [project(row, capability) for row in rows[: shown.page_size]]
    return Execution(
        CAMPUS_DATA,
        results=page,
        found=len(rows) if len(rows) > len(page) else None,
        shown=shown,
    )


def project(record: dict[str, Any], capability: str) -> dict[str, Any]:
    """One record, cut down to the fields the capability publishes."""
    entry = capability_for(capability)
    if entry is None:
        raise Unsupported("Rocky cannot look that up yet.") from LaneFailed(
            f"no capability named {capability!r}"
        )
    return {name: read(record) for name, read in entry.read.items() if name in entry.fields}
