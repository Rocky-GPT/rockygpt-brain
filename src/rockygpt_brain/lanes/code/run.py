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
from rockygpt_brain.brain.plan.schema import Filter, Operation, Plan
from rockygpt_brain.capabilities.entities import EntityResolutionFailed
from rockygpt_brain.capabilities.registry import Capability, capability_for
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

    semantic_filters = checked.filter_values
    try:
        execution_filters = (
            await entry.normalize(semantic_filters, now, data)
            if entry.normalize is not None
            else semantic_filters
        )
        records = await entry.execute(execution_filters, now, data)
    except EntityResolutionFailed as exc:
        raise Unsupported("Rocky could not resolve that campus name.") from exc
    except DataUnavailable as exc:
        raise DatasetUnavailable("Rocky could not reach campus data just now.") from exc

    return replace(
        apply(records, checked.operation, capability, frozenset(execution_filters)),
        looked_for={"capability": capability, "filters": semantic_filters},
        normalized_plan=checked.model_copy(
            update={
                "filters": [
                    Filter(field=name, value=value) for name, value in execution_filters.items()
                ]
            }
        ).summary(),
    )


def apply(
    records: list[dict[str, Any]],
    operation: Operation,
    capability: str,
    narrowed: frozenset[str] = frozenset(),
) -> Execution:
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
    # `select` takes the one row the ordering names; `limit` takes the number
    # the question asked for. Only a plan that said which gets fewer rows.
    if operation.select and selects_one(entry, narrowed):
        rows = rows[:1]
    elif operation.limit is not None:
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


def selects_one(entry: Capability, narrowed: frozenset[str]) -> bool:
    """Whether taking the first row leaves an answer out.

    A capability naming `parallel` fields is saying its rows can be several
    answers to one question rather than several candidates for one answer. Where
    the lookup narrowed on none of them, the question did not tell those rows
    apart and neither may an ordering: "the last day to register" reached the
    planner as one ordered thing twice — as `limit: 1`, then as `select` once
    the count was refused — and both times named a Session I add/drop deadline
    as the answer while dropping two later deadlines with an equal claim to it.

    A count the question actually asked for is honoured either way. Asking for
    five of something says how many were wanted; being singular does not.
    """
    return not entry.parallel or bool(entry.parallel & narrowed)


def project(record: dict[str, Any], capability: str) -> dict[str, Any]:
    entry = capability_for(capability)
    if entry is None:
        raise Unsupported("Rocky cannot look that up yet.") from LaneFailed(
            f"no capability named {capability!r}"
        )
    return {name: read(record) for name, read in entry.read.items() if name in entry.fields}
