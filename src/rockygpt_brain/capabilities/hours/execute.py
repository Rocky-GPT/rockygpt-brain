"""Look up structured campus or dining hours."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.hours.normalize import matches, query
from rockygpt_brain.services.data import DataPort


def _tag(records: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [{**record, "kind": kind} for record in records]


async def run(filters: dict[str, str], now: datetime, data: DataPort) -> list[dict[str, Any]]:
    request = query(filters, now)
    kind = filters.get("kind", "").strip().casefold()
    if kind == "campus":
        records = _tag(await data.campus_hours(request), "campus")
    elif kind == "dining":
        records = _tag(await data.dining_hours(request), "dining")
    elif kind:
        return []
    else:
        campus, dining = await asyncio.gather(
            data.campus_hours(request), data.dining_hours(request)
        )
        records = _tag(campus, "campus") + _tag(dining, "dining")
    return [record for record in records if matches(record, filters)]
