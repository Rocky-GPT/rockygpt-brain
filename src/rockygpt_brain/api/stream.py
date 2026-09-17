"""Optional SSE transport: operation context, labeled draft previews, then one final result."""

import asyncio
import json
from collections.abc import AsyncGenerator, Callable
from threading import Event
from typing import Any

from fastapi.responses import JSONResponse

from rockygpt_brain.progress import ProgressUpdate


def event(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def stream_turn(
    worker: asyncio.Task[dict[str, object] | JSONResponse],
    updates: asyncio.Queue[ProgressUpdate],
    stopped: Event,
    allowance_seconds: float,
    failure: Callable[[int, str], JSONResponse],
) -> AsyncGenerator[str, None]:
    deadline = asyncio.get_running_loop().time() + allowance_seconds
    pending: asyncio.Task[ProgressUpdate] | None = None
    try:
        yield event("progress", {"stage": "connecting"})
        while not worker.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            pending = asyncio.create_task(updates.get())
            done, _ = await asyncio.wait(
                {worker, pending}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if pending in done:
                yield event("progress", dict(pending.result()))
            else:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            pending = None
            if not done:
                break
        if not worker.done():
            stopped.set()
            result: dict[str, object] | JSONResponse = failure(504, "model_timeout")
        else:
            try:
                result = worker.result()
            except Exception:
                result = failure(503, "model_unreachable")
        if isinstance(result, JSONResponse):
            yield event(
                "result", {"status": result.status_code, "body": json.loads(bytes(result.body))}
            )
        else:
            yield event("result", {"status": 200, "body": result})
    finally:
        # Cancellation does not release the worker's slot or discard accounting.
        # Its next stage boundary stops new work; its own finally does cleanup.
        stopped.set()
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
