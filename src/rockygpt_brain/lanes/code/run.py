from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from rockygpt_brain.brain.execute.schema import (
    CAMPUS_DATA,
    Execution,
    Ordering,
    present,
)
from rockygpt_brain.brain.plan.schema import Operation, Plan
from rockygpt_brain.capabilities.registry import capability_for
from rockygpt_brain.errors import DatasetUnavailable, Unsupported
from rockygpt_brain.services.data import DataPort, DataUnavailable


class LaneFailed(Exception):
    pass


async def run(checked: Plan, now: datetime, data: DataPort) -> Execution:
    capability = checked.capability or ""
    entry = capability_for(capability)
    if entry is None:
        raise Unsupported("Rocky cannot look that up yet.") from LaneFailed(
            f"no capability named {capability!r}"
        )

    try:
        records = await entry.execute(checked.filter_values, now, data)
    except DataUnavailable as exc:
        raise DatasetUnavailable("Rocky could not reach campus data just now.") from exc

    return replace(
        apply(records, checked.operation, capability),
        looked_for={"capability": capability, "filters": checked.filter_values},
    )


def apply(records: list[dict[str, Any]], operation: Operation, capability: str) -> Execution:
    rows = list(records)
    entry = capability_for(capability)
    if entry is None:
        raise Unsupported("Rocky cannot look that up yet.") from LaneFailed(
            f"no capability named {capability!r}"
        )
    ordering = None
    if operation.order_by:
        key = entry.sort.get(operation.order_by) or entry.read.get(operation.order_by)
        if key is not None:
            rows.sort(key=key, reverse=operation.direction == "descending")
            ordering = Ordering(operation.order_by, operation.direction)
    if operation.count:
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
        ordering=ordering,
    )


def project(record: dict[str, Any], capability: str) -> dict[str, Any]:
    entry = capability_for(capability)
    if entry is None:
        raise Unsupported("Rocky cannot look that up yet.") from LaneFailed(
            f"no capability named {capability!r}"
        )
    return {name: read(record) for name, read in entry.read.items() if name in entry.fields}
