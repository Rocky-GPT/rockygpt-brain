"""In-process pub/sub used by GET /v1/admin/logs/stream.

Per-process only (DESIGN.md §7): a multi-instance deployment's operator
dashboard only sees changes written by the instance it happens to be
connected to. Acceptable for a development/operator surface.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Final

# A `Queue[None]` makes `await queue.get()` a call whose result type is
# exactly `None`, which mypy's `func-returns-value` check treats as "not a
# value" — yielding it directly (`yield await queue.get()`) is flagged even
# though it is type-correct for `AsyncIterator[None]`. Using a sentinel
# object as the queue's payload, and yielding `None` explicitly afterward,
# sidesteps that without changing behavior: the payload is never inspected,
# only awaited as a wake-up signal.
_CHANGE_SIGNAL: Final[object] = object()


class ChangeBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[object]] = set()

    def publish(self) -> None:
        for queue in list(self._subscribers):
            if not queue.full():
                queue.put_nowait(_CHANGE_SIGNAL)

    async def subscribe(self) -> AsyncIterator[None]:
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        try:
            while True:
                await queue.get()
                yield None
        finally:
            self._subscribers.discard(queue)
