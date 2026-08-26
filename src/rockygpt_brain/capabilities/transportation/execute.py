"""Look up structured campus transportation departures."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.transportation.normalize import query
from rockygpt_brain.services.data import DataPort


async def run(filters: dict[str, str], now: datetime, data: DataPort) -> list[dict[str, Any]]:
    return await data.transportation(query(filters, now))
