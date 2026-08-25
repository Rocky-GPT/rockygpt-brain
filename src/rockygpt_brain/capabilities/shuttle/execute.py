"""Look up shuttle departures.

Takes the filters a plan named, not the plan. A capability has no business
knowing what a lane is, which operations exist, or that a `Plan` type exists
at all — it is handed what to narrow by and returns what it found.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.shuttle.normalize import query
from rockygpt_brain.services.data import DataPort


async def run(filters: dict[str, str], now: datetime, data: DataPort) -> list[dict[str, Any]]:
    return await data.shuttle(query(filters, now))
