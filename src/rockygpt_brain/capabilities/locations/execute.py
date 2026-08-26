"""Resolve structured campus map locations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.locations.normalize import matches, query
from rockygpt_brain.services.data import DataPort


async def run(filters: dict[str, str], now: datetime, data: DataPort) -> list[dict[str, Any]]:
    records = await data.locations(query(filters, now))
    return [record for record in records if matches(record, filters)]
