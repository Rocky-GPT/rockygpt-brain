"""Background retention loop.

Runs as an asyncio task for the lifetime of the process (started in
app.py's lifespan), enforcing the expiry windows documented in
DESIGN.md §6 / spec/acceptance.md without depending on an external
scheduler.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from rockygpt_brain.persistence.chat_logs import purge_expired

logger = logging.getLogger("rockygpt_brain.purge")

DEFAULT_INTERVAL_SECONDS = 3600


async def run_purge_loop(
    pool: asyncpg.Pool, *, interval_seconds: float = DEFAULT_INTERVAL_SECONDS
) -> None:
    while True:
        try:
            cleared, deleted = await purge_expired(pool)
            if cleared or deleted:
                logger.info(
                    "retention purge complete", extra={"cleared": cleared, "deleted": deleted}
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a purge failure must not kill the process
            logger.exception("retention purge failed")
        await asyncio.sleep(interval_seconds)
