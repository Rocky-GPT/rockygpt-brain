from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from rockygpt_brain.brain.execute.schema import (
    CAMPUS_DATA,
    Execution,
    Ordering,
    present,
    represented,
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
        apply(records, checked.operation, capability, execution_filters),
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
    narrowed: Mapping[str, str] | None = None,
) -> Execution:
    rows = list(records)
    filters = narrowed or {}
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
    if operation.select and selects_one(entry, frozenset(filters), rows):
        rows = rows[:1]
    elif operation.limit is not None:
        rows = rows[: operation.limit]
    shown = present(len(rows))
    kept = paged(rows, entry, filters, shown.page_size)
    page = [project(rows[index], capability) for index in kept]
    return Execution(
        CAMPUS_DATA,
        results=page,
        found=len(rows) if len(rows) > len(page) else None,
        shown=shown,
        ordering=ordering,
    )


def paged(
    rows: list[dict[str, Any]],
    entry: Capability,
    narrowed: Mapping[str, str],
    capacity: int,
) -> list[int]:
    """Which rows go on the page.

    The first `capacity` of them, unless the question named several values of a
    grouping filter and more than one of those values actually matched — then
    every one of them gets a place, because a compound question stays compound
    all the way to the answer or it was not answered. `represented` says how the
    capacity is shared; this decides only whether it applies.
    """
    for name, field_name in entry.groups.items():
        wanted = [value.strip() for value in narrowed.get(name, "").split(",") if value.strip()]
        if len(wanted) < 2:
            continue
        read = entry.read.get(field_name) or entry.sort.get(field_name)
        if read is None:
            continue
        groups = []
        for value in wanted:
            group = [
                index
                for index, row in enumerate(rows)
                if str(read(row)).casefold() == value.casefold()
            ]
            if group:
                groups.append(group)
        # One group present is the ordinary case wearing a plural filter: a page
        # of it is already a page of everything there was.
        if len(groups) > 1:
            return represented(groups, capacity)
    return list(range(min(capacity, len(rows))))


def selects_one(entry: Capability, narrowed: frozenset[str], rows: list[dict[str, Any]]) -> bool:
    """Whether taking the first row leaves an answer out.

    A capability naming `parallel` fields is saying its rows can be several
    answers to one question rather than several candidates for one answer. Where
    the question did not tell those rows apart, neither may an ordering: "the
    last day to register" reached the planner as one ordered thing twice — as
    `limit: 1`, then as `select` once the count was refused — and both times
    named a Session I add/drop deadline as the answer while dropping two later
    deadlines with an equal claim to it.

    The test is the rows, not the filter names. This asked only whether the
    lookup narrowed on *any* parallel field, which reads as "the question was
    specific enough" and is not the same thing: `calendar` is parallel along two
    axes at once — a term runs several sessions and files several kinds of
    deadline in each — so narrowing on `kind` says nothing about whether what
    matched still spans sessions. Asked when grades are due, a plan naming the
    kind and the term but no session matched both the Session I deadline in
    October and the full-semester one in December, and `select` dropped
    December. That is the same answer going missing that `parallel` was added
    to prevent, one axis over.

    So: look at what actually matched. Rows that agree on every parallel field
    the question left open are candidates for one answer, and an ordering picks
    it. Rows that differ on one are answers to different questions the asker
    asked at once, and all of them survive.

    A count the question actually asked for is honoured either way. Asking for
    five of something says how many were wanted; being singular does not.
    """
    for name in entry.parallel - narrowed:
        read = entry.read.get(name) or entry.sort.get(name)
        if read is None:
            continue
        if len({str(read(row)) for row in rows}) > 1:
            return False
    return True


def project(record: dict[str, Any], capability: str) -> dict[str, Any]:
    entry = capability_for(capability)
    if entry is None:
        raise Unsupported("Rocky cannot look that up yet.") from LaneFailed(
            f"no capability named {capability!r}"
        )
    return {name: read(record) for name, read in entry.read.items() if name in entry.fields}
