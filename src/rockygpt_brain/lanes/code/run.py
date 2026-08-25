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
from rockygpt_brain.brain.plan.schema import Operation, Plan
from rockygpt_brain.capabilities.registry import CAPABILITIES
from rockygpt_brain.errors import ServiceError
from rockygpt_brain.services.data import DataPort, DataUnavailable


class LaneFailed(Exception):
    """Why a lane produced nothing. The cause of the ServiceError."""


async def run(checked: Plan, now: datetime, data: DataPort) -> Execution:
    capability = checked.capability or ""
    entry = CAPABILITIES.get(capability)
    if entry is None:
        raise ServiceError(
            503, "SERVICE_UNAVAILABLE", "Rocky cannot look that up yet.", retryable=False
        ) from LaneFailed(f"no capability named {capability!r}")

    try:
        # The filters, not the plan. A capability has no business knowing what
        # a lane is or which operations exist.
        records = await entry.execute(checked.filter_values, now, data)
    except DataUnavailable as exc:
        raise ServiceError(
            503,
            "DATASET_UNAVAILABLE",
            "Rocky could not reach campus data just now.",
            retryable=True,
        ) from exc

    results, count = apply(records, checked.operation, capability)
    return Execution(CAMPUS_DATA, results=results, count=count)


def apply(
    records: list[dict[str, Any]], operation: Operation, capability: str
) -> tuple[list[dict[str, Any]], int | None]:
    """`orderBy`, `limit` and `count`, over whatever the lookup returned."""
    rows = list(records)
    entry = CAPABILITIES[capability]
    if operation.order_by:
        key = entry.sort.get(operation.order_by) or entry.read.get(operation.order_by)
        if key is not None:
            rows.sort(key=key, reverse=operation.direction == "descending")
    if operation.count:
        # Counted before the limit: the answer is how many matched, not how
        # many were kept.
        return [], len(rows)
    if operation.limit is not None:
        rows = rows[: operation.limit]
    return [project(row, capability) for row in rows], None


def project(record: dict[str, Any], capability: str) -> dict[str, Any]:
    """One record, cut down to the fields the capability publishes."""
    entry = CAPABILITIES[capability]
    return {name: read(record) for name, read in entry.read.items() if name in entry.fields}
