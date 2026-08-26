"""Run a capability, then apply the plan's operation to what came back.

The split is deliberate. The capability knows how to fetch and how to read its
own records; this knows `orderBy`, `limit` and `count`, which mean the same
thing whatever was looked up. Neither has to learn the other's job, and the
registry is what joins them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rockygpt_brain.brain.execute.schema import CAMPUS_DATA, Execution
from rockygpt_brain.brain.plan.schema import MOST_ROWS, Operation, Plan
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

    results, count, found = apply(records, checked.operation, capability)
    return Execution(
        CAMPUS_DATA,
        results=results,
        count=count,
        found=found,
        looked_for={"capability": capability, "filters": checked.filter_values},
    )


#: How many rows reach BRAIN #3 when the plan asks for no particular number.
#: The same ceiling a plan may ask up to, because it is the same constraint.
#:
#: The data service will hand over its whole table now, which is right for
#: something browsing the data and wrong for something writing a sentence: the
#: course catalogue is 3,344 entries and three megabytes, and a question like
#: "what courses does Ramapo offer" put all of it in the prompt and failed the
#: turn outright. A list answer was never going to read out three thousand
#: rows, so this is the point past which more rows stop improving the answer.
#:
#: Only applies when the plan named no `limit`. A plan that asks for a number
#: gets that number — deciding how much of a result to use is its job, and this
#: is only the fallback for when it did not say.
GROUNDING_ROWS = MOST_ROWS


def apply(
    records: list[dict[str, Any]], operation: Operation, capability: str
) -> tuple[list[dict[str, Any]], int | None, int | None]:
    """`orderBy`, `limit` and `count`, over whatever the lookup returned.

    Returns the rows, the count if one was asked for, and — only when the
    grounding cap cut the result — how many there were before it did.

    That third value is the whole point of the cap being honest. `limit` is
    what the question asked for, and a result cut to it is the answer. The
    grounding cap is not: it is a fact about how much a model can be handed,
    and a result cut to it is a sample. Silently they look identical, which is
    how "200 rows" becomes "the courses Ramapo offers".
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
        return [], len(rows), None
    if operation.limit is not None:
        # What the question asked for. Cutting to it is the answer, not a
        # sample of one, so nothing is reported as missing.
        return [project(row, capability) for row in rows[: operation.limit]], None, None
    kept = rows[:GROUNDING_ROWS]
    found = len(rows) if len(rows) > len(kept) else None
    return [project(row, capability) for row in kept], None, found


def project(record: dict[str, Any], capability: str) -> dict[str, Any]:
    """One record, cut down to the fields the capability publishes."""
    entry = capability_for(capability)
    if entry is None:
        raise Unsupported("Rocky cannot look that up yet.") from LaneFailed(
            f"no capability named {capability!r}"
        )
    return {name: read(record) for name, read in entry.read.items() if name in entry.fields}
