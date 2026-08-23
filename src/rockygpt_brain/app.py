"""FastAPI application factory: middleware, routers, exception handlers,
and process lifespan (startup/shutdown of the DB pool, data client, model
client, rate limiters, change bus, and retention purge loop).

**Settings identity.** `create_app(settings=...)` and the lifespan it wires
up must observe the exact same `Settings` instance — a test that injects a
custom `Settings(...)` (e.g. to enable admin routes or set specific rate
limits) would otherwise see the middleware/router-mounting decisions use
that instance while the lifespan silently re-resolved a different one via
`get_settings()`. `_build_lifespan` is a factory that closes over the
already-resolved `settings` passed to `create_app`, rather than the
lifespan calling `get_settings()` itself.

**Partial-startup and shutdown cleanup.** Resource variables (`db_pool`,
`data_client`, `model_client`, `purge_task`) start `None` and are only
assigned once each resource is actually created. Both the startup-failure
path and normal shutdown call the *same* `_cleanup_resources` helper, which
closes every non-`None` resource concurrently via
`asyncio.gather(..., return_exceptions=True)` — so one resource's close
failure can never skip or interrupt closing the others, and can never
replace the original exception that triggered cleanup: a close failure is
only ever logged, with fixed metadata (which resources failed, never
secrets or exception text), and is never re-raised in cleanup's own right.
On the startup-failure path this means `_cleanup_resources` itself never
raises, so the bare `raise` after it always re-propagates the *original*
startup exception, unmodified. `BaseException` (not `Exception`) is caught
around startup specifically so a cancellation *during* startup also
triggers this same cleanup before propagating. `db_pool` staying `None`
(either because `DATABASE_URL` isn't configured, or because startup failed
before it was assigned to `app.state`) is exactly what `/readiness`
already treats as the database dependency failing (api/probes.py).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from rockygpt_brain.api import admin, chat, feedback, probes
from rockygpt_brain.api.middleware import RequestContextMiddleware
from rockygpt_brain.brain.model_client import OpenAIModelClient
from rockygpt_brain.config import Settings, get_settings
from rockygpt_brain.data_client.client import DataServiceClient
from rockygpt_brain.errors import AppError, ErrorCode
from rockygpt_brain.observability.change_bus import ChangeBus
from rockygpt_brain.observability.logging import configure_logging
from rockygpt_brain.observability.request_id import new_request_id
from rockygpt_brain.persistence.db import apply_schema, create_pool
from rockygpt_brain.persistence.purge import run_purge_loop
from rockygpt_brain.schemas.common import ErrorDetail, ErrorResponse
from rockygpt_brain.security.rate_limit import FixedWindowRateLimiter

logger = logging.getLogger("rockygpt_brain.app")

CHAT_RATE_LIMIT = (20, 60)
GLOBAL_CHAT_RATE_LIMIT = (200, 60)
FEEDBACK_RATE_LIMIT = (60, 60)
GLOBAL_FEEDBACK_RATE_LIMIT = (300, 60)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_request_id()


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        request_id=_request_id(request),
        error=ErrorDetail(
            code=code,
            message=message,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
        ),
    )
    headers = {"Retry-After": str(retry_after_seconds)} if retry_after_seconds else None
    return JSONResponse(
        status_code=status_code, content=body.model_dump(by_alias=True), headers=headers
    )


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    # Starlette's add_exception_handler requires a Callable[[Request,
    # Exception], ...] signature (it is contravariant in the exception
    # type — a handler narrowed to a subclass isn't a valid substitute
    # from a type-checker's perspective, even though Starlette only ever
    # calls a handler registered for AppError with an AppError instance).
    # `isinstance` + `raise` (not `assert`, which -O can strip) both
    # narrows for mypy and is a real, always-on runtime check that this
    # handler was wired to the exception type it expects.
    if not isinstance(exc, AppError):
        raise TypeError("_handle_app_error registered for a non-AppError exception type")
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        retry_after_seconds=exc.retry_after_seconds,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise TypeError(
            "_handle_validation_error registered for a non-RequestValidationError type"
        )
    return _error_response(
        request,
        status_code=400,
        code="INVALID_REQUEST",
        message="The request is malformed or violates a documented bound.",
        retryable=False,
    )


_HTTP_STATUS_CODES: dict[int, ErrorCode] = {
    401: "UNAUTHORIZED",
    404: "NOT_FOUND",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
}


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError(
            "_handle_http_exception registered for a non-HTTPException type"
        )
    code = _HTTP_STATUS_CODES.get(
        exc.status_code, "INVALID_REQUEST" if exc.status_code < 500 else "INTERNAL_ERROR"
    )
    return _error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message="The request could not be processed.",
        retryable=exc.status_code >= 500,
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception", extra={"route": request.url.path})
    return _error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected service error occurred.",
        retryable=False,
    )


async def _cleanup_resources(
    *,
    purge_task: asyncio.Task[None] | None,
    data_client: DataServiceClient | None,
    model_client: OpenAIModelClient | None,
    db_pool: asyncpg.Pool | None,
) -> None:
    """Close every non-None resource, concurrently, best-effort. Used for
    both a partial-startup-failure teardown and normal shutdown. Never
    raises itself — a failure to close one resource is only logged (fixed
    metadata, never secrets or exception text) and never prevents the
    others from being attempted, and never replaces whatever exception (if
    any) the caller is already propagating."""
    if purge_task is not None:
        purge_task.cancel()
        await asyncio.gather(purge_task, return_exceptions=True)

    closers: list[tuple[str, Coroutine[Any, Any, Any]]] = []
    if data_client is not None:
        closers.append(("data_client", data_client.aclose()))
    if model_client is not None:
        closers.append(("model_client", model_client.aclose()))
    if db_pool is not None:
        closers.append(("db_pool", db_pool.close()))
    if not closers:
        return

    results = await asyncio.gather(
        *(coro for _name, coro in closers), return_exceptions=True
    )
    failed = [
        name
        for (name, _coro), result in zip(closers, results, strict=True)
        if isinstance(result, BaseException)
    ]
    if failed:
        logger.error("resource cleanup failed", extra={"failed_count": len(failed)})


def _build_lifespan(
    settings: Settings,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(level="INFO" if settings.is_production else "DEBUG")
        app.state.settings = settings

        db_pool: asyncpg.Pool | None = None
        purge_task: asyncio.Task[None] | None = None
        data_client: DataServiceClient | None = None
        model_client: OpenAIModelClient | None = None

        try:
            database_url = (
                settings.database_url.get_secret_value() if settings.database_url else None
            )
            if database_url:
                if (
                    not settings.chat_log_hash_key
                    or not settings.chat_log_hash_key.get_secret_value()
                ):
                    raise RuntimeError(
                        "CHAT_LOG_HASH_KEY is required whenever DATABASE_URL is configured."
                    )
                db_pool = await create_pool(database_url)
                await apply_schema(db_pool)
                purge_task = asyncio.create_task(run_purge_loop(db_pool))

            environment_token = (
                settings.staging_service_token.get_secret_value()
                if settings.staging_service_token
                else None
            )
            data_client = DataServiceClient(
                base_url=settings.data_url, environment_token=environment_token
            )

            api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
            model_client = OpenAIModelClient(api_key=api_key, model=settings.openai_chat_model)
        except BaseException:
            await _cleanup_resources(
                purge_task=purge_task,
                data_client=data_client,
                model_client=model_client,
                db_pool=db_pool,
            )
            raise

        app.state.db_pool = db_pool
        app.state.data_client = data_client
        app.state.model_client = model_client
        app.state.chat_rate_limiter = FixedWindowRateLimiter(
            limit=CHAT_RATE_LIMIT[0], window_seconds=CHAT_RATE_LIMIT[1]
        )
        app.state.global_chat_rate_limiter = FixedWindowRateLimiter(
            limit=GLOBAL_CHAT_RATE_LIMIT[0], window_seconds=GLOBAL_CHAT_RATE_LIMIT[1]
        )
        app.state.feedback_rate_limiter = FixedWindowRateLimiter(
            limit=FEEDBACK_RATE_LIMIT[0], window_seconds=FEEDBACK_RATE_LIMIT[1]
        )
        app.state.global_feedback_rate_limiter = FixedWindowRateLimiter(
            limit=GLOBAL_FEEDBACK_RATE_LIMIT[0], window_seconds=GLOBAL_FEEDBACK_RATE_LIMIT[1]
        )
        app.state.change_bus = ChangeBus()

        try:
            yield
        finally:
            await _cleanup_resources(
                purge_task=purge_task,
                data_client=data_client,
                model_client=model_client,
                db_pool=db_pool,
            )

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    app = FastAPI(
        title="RockyGPT Brain Clean-room API",
        version="1.0.0",
        lifespan=_build_lifespan(resolved_settings),
    )

    app.add_middleware(RequestContextMiddleware, settings=resolved_settings)

    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)

    app.include_router(probes.router)
    app.include_router(chat.router)
    app.include_router(feedback.router)
    if resolved_settings.admin_enabled:
        app.include_router(admin.router)

    return app
