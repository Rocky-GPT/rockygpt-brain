from __future__ import annotations

from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.calendar.normalize import matches, query, record_date
from rockygpt_brain.services.data import DataPort


async def run(filters: dict[str, str], now: datetime, data: DataPort) -> list[dict[str, Any]]:
    records = await data.calendar(query(filters, now))
    matched = [record for record in records if matches(record, filters)]
    if filters.get("termId") or filters.get("date") or not matched:
        return matched

    # A broad deadline question belongs to the nearest applicable term. Keep
    # every matching session in that term; choosing one session would silently
    # turn a valid second deadline into missing data.
    by_term: dict[str, list[dict[str, Any]]] = {}
    for record in matched:
        key = str(record.get("termId") or record.get("term") or "")
        by_term.setdefault(key, []).append(record)
    nearest = min(
        by_term,
        key=lambda key: min(
            (record_date(record) or datetime.max.date()).toordinal()
            for record in by_term[key]
        ),
    )
    return by_term[nearest]
