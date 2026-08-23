"""GET /health and GET /readiness — always public, per spec/system-boundaries
.md's shared security boundary ("/health and /readiness remain
unauthenticated for probes").

Readiness treats both the brain's own database and the data service as
required dependencies (DESIGN.md §8): a missing `db_pool`/`data_client` on
`app.state` counts as that dependency failing, not as "not applicable."
Both checks run concurrently under one shared, total deadline
(`READINESS_TIMEOUT_SECONDS`) via `asyncio.wait`, so the endpoint's total
latency is bounded regardless of how many dependencies exist — sequential
per-dependency waits could otherwise sum past the 3-second acceptance
budget. A dependency whose check hasn't finished when the deadline expires
is cancelled *and drained* (awaited with `return_exceptions=True`) before
the response is built, so a cancelled httpx/asyncpg call can't keep running
in the background after the response is sent, or log an unretrieved-
exception warning; the endpoint does not raise on timeout, it returns `503`
with that dependency listed. External cancellation of the request itself
(not just the internal deadline) is not swallowed: draining only awaits the
child check tasks, never the request-handling task itself, so an outer
`CancelledError` continues propagating unchanged after the `finally` block
completes — it is never converted into a readiness response.
"""

from __future__ import annotations

import asyncio
import time

import asyncpg
from fastapi import APIRouter, Request, Response
from starlette.status import HTTP_200_OK, HTTP_503_SERVICE_UNAVAILABLE

from rockygpt_brain.data_client.client import DataServiceClient
from rockygpt_brain.data_client.errors import DataClientError
from rockygpt_brain.persistence.db import ping
from rockygpt_brain.schemas.common import Health, Readiness

router = APIRouter()

_PROCESS_STARTED_AT = time.monotonic()
READINESS_TIMEOUT_SECONDS = 2.5


@router.get("/health")
async def get_health() -> Health:
    # Must not depend on the model, data service, or database (spec).
    return Health(
        status="healthy",
        service="rockygpt-brain",
        uptimeSeconds=time.monotonic() - _PROCESS_STARTED_AT,
    )


async def _check_database(pool: asyncpg.Pool | None) -> bool:
    if pool is None:
        return False
    return await ping(pool, timeout_seconds=READINESS_TIMEOUT_SECONDS)


async def _check_dataset(data_client: DataServiceClient | None) -> bool:
    if data_client is None:
        return False
    try:
        payload = await data_client.readiness()
    except DataClientError:
        return False
    return isinstance(payload, dict) and payload.get("status") == "ready"


def _task_ok(task: asyncio.Task[bool], done: set[asyncio.Task[bool]]) -> bool:
    if task not in done or task.cancelled():
        return False
    try:
        return task.result()
    except Exception:  # noqa: BLE001 - never reflect exception details, just fail closed
        return False


@router.get("/readiness")
async def get_readiness(request: Request, response: Response) -> Readiness:
    pool = getattr(request.app.state, "db_pool", None)
    data_client = getattr(request.app.state, "data_client", None)

    database_task: asyncio.Task[bool] = asyncio.ensure_future(_check_database(pool))
    dataset_task: asyncio.Task[bool] = asyncio.ensure_future(_check_dataset(data_client))
    tasks = {database_task, dataset_task}

    try:
        done, _pending = await asyncio.wait(tasks, timeout=READINESS_TIMEOUT_SECONDS)
    finally:
        # Cancel and drain anything still running so a slow httpx/asyncpg
        # call can't keep executing after the response is sent, or leave an
        # unretrieved-exception warning behind. This only awaits the child
        # check tasks, never the current (request-handling) task, so an
        # external cancellation of *this* request is untouched by it and
        # continues propagating normally once this block finishes.
        unfinished = [task for task in tasks if not task.done()]
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.gather(*unfinished, return_exceptions=True)

    failing: list[str] = []
    if not _task_ok(database_task, done):
        failing.append("database")
    if not _task_ok(dataset_task, done):
        failing.append("dataset")

    if failing:
        response.status_code = HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(status="unready", failing=failing)
    response.status_code = HTTP_200_OK
    return Readiness(status="ready", failing=[])
