"""GET/POST /v1/admin/logs*.

Only ever mounted when `settings.admin_enabled` (app.py) — otherwise these
paths simply aren't registered and answer `404`, matching "Operator routes
are not registered in this environment." Every route additionally enforces
its own `AdminBearer` check (`_require_admin`) independent of that mounting
decision and independent of the shared environment-token gate, per
spec/acceptance.md: "Admin endpoints independently enforce bearer
authentication."

Every database read/write here is bounded to `ADMIN_DB_TIMEOUT_SECONDS`; a
timeout or database-layer failure maps to the documented `503
SERVICE_UNAVAILABLE` envelope with a fixed message, never exception
details. Nothing here catches `BaseException` or `CancelledError`, so
external request cancellation still propagates.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import asyncpg
from fastapi import APIRouter, Query, Request, Response
from starlette.responses import StreamingResponse

from rockygpt_brain.api.parsing import MAX_ADMIN_BODY_BYTES, parse_json_body
from rockygpt_brain.errors import (
    InvalidRequestError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from rockygpt_brain.persistence.chat_logs import (
    get_version,
    list_logs,
    set_operator_feedback,
)
from rockygpt_brain.schemas.admin import (
    ChatLogItem,
    LogListResponse,
    LogMetrics,
    OperatorFeedbackRequest,
    UnmodifiedResponse,
)
from rockygpt_brain.schemas.feedback import FeedbackSuccess
from rockygpt_brain.security.admin_auth import extract_bearer_token, token_is_valid

router = APIRouter()

HEARTBEAT_INTERVAL_SECONDS = 15.0
ADMIN_DB_TIMEOUT_SECONDS = 5.0

_VALID_ORIGINS = frozenset({"client", "dev", "bot"})


def _require_admin(request: Request) -> None:
    settings = request.app.state.settings
    expected = settings.admin_api_token.get_secret_value() if settings.admin_api_token else ""
    presented = extract_bearer_token(request.headers.get("authorization"))
    if not expected or not token_is_valid(presented=presented, expected=expected):
        raise UnauthorizedError("A valid admin bearer token is required.")


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _parse_origins(value: str | None) -> list[str] | None:
    items = _split_csv(value)
    if items is None:
        return None
    for item in items:
        if item not in _VALID_ORIGINS:
            raise InvalidRequestError("origin must contain only: client, dev, bot.")
    return items


# response_model is set explicitly to the two real modeled bodies. FastAPI
# would otherwise infer it from the function's return type annotation
# below, which also includes `Response` (used only for the bare, bodyless
# 304 reply) — building a Pydantic field from a union that includes a raw
# Starlette `Response` class fails at route-registration time. This
# doesn't weaken the documented contract: spec/brain-api.openapi.yaml's
# `304` response has no `content`/body schema at all, so excluding it from
# response_model matches the spec rather than diverging from it. Returning
# an actual `Response` instance from the handler still bypasses
# response_model validation for that one case, same as always in FastAPI.
@router.get("/v1/admin/logs", response_model=LogListResponse | UnmodifiedResponse)
async def list_chat_logs(
    request: Request,
    response: Response,
    search: str | None = Query(default=None, max_length=200),
    route: str | None = Query(default=None, max_length=300),
    origin: str | None = Query(default=None, max_length=64),
    version: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=100, ge=1, le=100),
) -> LogListResponse | UnmodifiedResponse | Response:
    _require_admin(request)
    routes = _split_csv(route)
    origins = _parse_origins(origin)
    pool = request.app.state.db_pool
    if pool is None:
        raise ServiceUnavailableError("Persistence is not configured.")

    try:
        async with asyncio.timeout(ADMIN_DB_TIMEOUT_SECONDS):
            current_version = await get_version(pool)
            etag = f'"{current_version}"'
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304)
            if version is not None and version == current_version:
                return UnmodifiedResponse(modified=False)
            page = await list_logs(pool, search=search, routes=routes, origins=origins, limit=limit)
    except TimeoutError as exc:
        raise ServiceUnavailableError("Persistence timed out.") from exc
    except (asyncpg.PostgresError, OSError) as exc:
        raise ServiceUnavailableError("Persistence is temporarily unavailable.") from exc

    response.headers["ETag"] = f'"{page.version}"'
    return LogListResponse(
        logs=[ChatLogItem.model_validate(row) for row in page.logs],
        metrics=LogMetrics(**page.metrics),
        version=page.version,
    )


@router.post("/v1/admin/logs/feedback", response_model=FeedbackSuccess)
async def set_operator_log_feedback(request: Request) -> FeedbackSuccess:
    _require_admin(request)
    body = await parse_json_body(
        request, OperatorFeedbackRequest, max_bytes=MAX_ADMIN_BODY_BYTES
    )
    pool = request.app.state.db_pool
    if pool is None:
        raise ServiceUnavailableError("Persistence is not configured.")

    try:
        async with asyncio.timeout(ADMIN_DB_TIMEOUT_SECONDS):
            updated = await set_operator_feedback(
                pool, log_id=body.log_id, feedback=body.feedback
            )
    except TimeoutError as exc:
        raise ServiceUnavailableError("Persistence timed out.") from exc
    except (asyncpg.PostgresError, OSError) as exc:
        raise ServiceUnavailableError("Persistence is temporarily unavailable.") from exc

    if not updated:
        raise NotFoundError("The requested log entry does not exist.")

    request.app.state.change_bus.publish()
    return FeedbackSuccess(success=True)


async def _event_stream(request: Request) -> AsyncIterator[str]:
    """Emit `data: {"type":"change"}` on each change-bus notification, and a
    heartbeat comment when `HEARTBEAT_INTERVAL_SECONDS` passes without one.
    Checked before waiting and again after each timeout/event: `TimeoutError`
    from the heartbeat window is the only exception caught here, so an
    external disconnect/cancellation (which surfaces as `CancelledError`, or
    simply as `request.is_disconnected()` becoming true) is either left
    uncaught or causes a clean early `return` — never swallowed. The
    subscription is always closed in `finally`, on every exit path.
    """
    subscription = request.app.state.change_bus.subscribe()
    try:
        while True:
            if await request.is_disconnected():
                return
            try:
                async with asyncio.timeout(HEARTBEAT_INTERVAL_SECONDS):
                    await subscription.__anext__()
                if await request.is_disconnected():
                    return
                yield 'data: {"type":"change"}\n\n'
            except TimeoutError:
                if await request.is_disconnected():
                    return
                yield ": heartbeat\n\n"
    finally:
        await subscription.aclose()


@router.get("/v1/admin/logs/stream")
async def stream_log_changes(request: Request) -> StreamingResponse:
    _require_admin(request)
    return StreamingResponse(
        _event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
