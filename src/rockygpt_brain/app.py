"""Frozen FastAPI compatibility surface for Hybrid V1."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from rockygpt_brain.brain import Brain, TurnIdentity
from rockygpt_brain.capabilities import ShuttleCapability
from rockygpt_brain.config import Settings, get_settings
from rockygpt_brain.contracts import (
    ChatRequest,
    ChatSuccess,
    ErrorDetail,
    ErrorResponse,
    FeedbackRequest,
    FeedbackSuccess,
    Health,
    LogListResponse,
    OperatorFeedbackRequest,
    Readiness,
    UnmodifiedResponse,
)
from rockygpt_brain.data_client import DataPort, HttpDataV2Client
from rockygpt_brain.errors import ServiceError
from rockygpt_brain.model import ModelPort, OpenAIResponsesModel
from rockygpt_brain.persistence import (
    Repository,
    ServiceSafeRepository,
    UnavailableRepository,
)
from rockygpt_brain.postgres_repository import PostgresRepository
from rockygpt_brain.security import (
    SlidingWindowRateLimiter,
    client_identity,
    pseudonymize,
    require_admin_bearer,
    require_shared_token,
)

ClientKeyHeader = Annotated[
    str | None,
    Header(alias="x-rockygpt-client-key"),
]
ClientSignatureHeader = Annotated[
    str | None,
    Header(alias="x-rockygpt-client-signature"),
]
EnvironmentHeader = Annotated[
    str | None,
    Header(alias="x-rockygpt-environment-token", min_length=1, max_length=512),
]
OriginHeader = Annotated[
    Literal["client", "dev", "bot"] | None,
    Header(alias="x-rockygpt-origin"),
]
AuthorizationHeader = Annotated[str | None, Header(alias="authorization")]


@dataclass(slots=True)
class AppServices:
    settings: Settings
    data: DataPort
    model: ModelPort
    repository: Repository
    brain: Brain
    chat_limiter: SlidingWindowRateLimiter
    feedback_limiter: SlidingWindowRateLimiter


def _json(
    model: object, status_code: int = 200, headers: dict[str, str] | None = None
) -> JSONResponse:
    if hasattr(model, "model_dump"):
        content = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    else:
        content = model
    return JSONResponse(content=content, status_code=status_code, headers=headers)


def _error_response(request_id: str, error: ServiceError) -> JSONResponse:
    headers: dict[str, str] = {"X-Request-Id": request_id}
    if error.retry_after_seconds is not None:
        headers["Retry-After"] = str(error.retry_after_seconds)
    return _json(
        ErrorResponse(
            request_id=request_id,
            error=ErrorDetail(
                code=error.code,
                message=error.public_message,
                retryable=error.retryable,
                retry_after_seconds=error.retry_after_seconds,
            ),
        ),
        status_code=error.status_code,
        headers=headers,
    )


def create_app(
    *,
    settings: Settings | None = None,
    data: DataPort | None = None,
    model: ModelPort | None = None,
    repository: Repository | None = None,
) -> FastAPI:
    config = settings or get_settings()
    data_port = data or HttpDataV2Client(
        config.data_url,
        environment_token=config.secret_value(config.staging_service_token),
    )
    model_port = model or OpenAIResponsesModel(
        api_key=config.secret_value(config.openai_api_key),
        model=config.openai_chat_model,
    )
    database_url = config.secret_value(config.database_url)
    repository_backend = repository or (
        PostgresRepository(
            database_url,
            recent_turn_limit=config.memory_recent_turns,
            claim_limit=config.memory_claims,
            text_retention_days=config.text_retention_days,
            metadata_retention_days=config.metadata_retention_days,
        )
        if database_url
        else UnavailableRepository()
    )
    repo = ServiceSafeRepository(repository_backend)
    brain = Brain(
        model=model_port,
        shuttle=ShuttleCapability(data_port),
        repository=repo,
        campus_timezone=config.campus_timezone,
    )
    services = AppServices(
        settings=config,
        data=data_port,
        model=model_port,
        repository=repo,
        brain=brain,
        chat_limiter=SlidingWindowRateLimiter(config.chat_rate_limit, config.rate_window_seconds),
        feedback_limiter=SlidingWindowRateLimiter(
            config.feedback_rate_limit, config.rate_window_seconds
        ),
    )
    started = time.monotonic()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async def initialize_and_purge() -> None:
            try:
                initialize = getattr(repository_backend, "initialize", None)
                if initialize is not None:
                    await initialize()
                await repo.purge_expired()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Dependency startup cannot remove the independent health/readiness surface.
                # Readiness and functional calls continue to fail closed until storage recovers.
                app.state.persistence_startup_failed = True
                return

        async def retention_loop() -> None:
            while True:
                await asyncio.sleep(3600)
                try:
                    await repo.purge_expired()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A transient cleanup fault must not terminate future retention attempts.
                    app.state.retention_failure_count += 1

        initialization_task = asyncio.create_task(initialize_and_purge())
        retention_task = asyncio.create_task(retention_loop())
        try:
            yield
        finally:
            for task in (initialization_task, retention_task):
                task.cancel()
            await asyncio.gather(initialization_task, retention_task, return_exceptions=True)
            for dependency in (data_port, repository_backend):
                close = getattr(dependency, "close", None)
                if close is not None:
                    with contextlib.suppress(Exception):
                        await close()

    app = FastAPI(
        title="RockyGPT Hybrid V1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.persistence_startup_failed = False
    app.state.retention_failure_count = 0

    @app.middleware("http")
    async def request_boundary(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        if request.method in {"POST", "PUT", "PATCH"}:
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    return _error_response(
                        request_id,
                        ServiceError(400, "INVALID_REQUEST", "Content-Length is invalid."),
                    )
                if declared_length > config.max_body_bytes:
                    return _error_response(
                        request_id,
                        ServiceError(413, "PAYLOAD_TOO_LARGE", "The request body is too large."),
                    )
            try:
                body = await request.body()
            except Exception:
                return _error_response(
                    request_id,
                    ServiceError(400, "INVALID_REQUEST", "The request body is invalid."),
                )
            if len(body) > config.max_body_bytes:
                return _error_response(
                    request_id,
                    ServiceError(413, "PAYLOAD_TOO_LARGE", "The request body is too large."),
                )
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        return _error_response(request.state.request_id, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, _: RequestValidationError) -> JSONResponse:
        return _error_response(
            request.state.request_id,
            ServiceError(400, "INVALID_REQUEST", "The request violates the API contract."),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 405:
            return _error_response(
                request.state.request_id,
                ServiceError(
                    405,
                    "INVALID_REQUEST",
                    "The request method is not allowed for this resource.",
                ),
            )
        return _error_response(
            request.state.request_id,
            ServiceError(
                exc.status_code if exc.status_code == 404 else 500,
                "NOT_FOUND" if exc.status_code == 404 else "INTERNAL_ERROR",
                "The requested resource was not found."
                if exc.status_code == 404
                else "An unexpected service error occurred.",
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, _: Exception) -> JSONResponse:
        return _error_response(
            request.state.request_id,
            ServiceError(500, "INTERNAL_ERROR", "An unexpected service error occurred."),
        )

    def check_environment(token: str | None) -> None:
        require_shared_token(token, config.secret_value(config.staging_service_token))

    def check_admin(authorization: str | None) -> None:
        require_admin_bearer(authorization, config.secret_value(config.admin_api_token))

    @app.get("/health", response_model=Health, response_model_exclude_none=True)
    async def health() -> Health:
        return Health(
            status="healthy",
            service="rockygpt-brain",
            uptimeSeconds=max(0.0, time.monotonic() - started),
        )

    @app.get("/readiness", response_model=Readiness, response_model_exclude_none=True)
    async def readiness() -> Response:
        failing: list[str] = []
        if not model_port.configured:
            failing.append("model")
        try:
            data_ready, database_ready = await asyncio.wait_for(
                asyncio.gather(data_port.readiness(), repo.readiness()), timeout=2.8
            )
        except TimeoutError:
            data_ready, database_ready = False, False
        if not data_ready:
            failing.append("data")
        if not database_ready:
            failing.append("database")
        result = Readiness(
            status="unready" if failing else "ready",
            failing=failing or None,
            timestamp=datetime.now(UTC),
        )
        return _json(result, status_code=503 if failing else 200)

    @app.post(
        "/v1/chat",
        response_model=ChatSuccess,
        response_model_by_alias=True,
        response_model_exclude_none=True,
    )
    async def chat(
        request: Request,
        body: ChatRequest,
        environment_token: EnvironmentHeader = None,
        client_key: ClientKeyHeader = None,
        client_signature: ClientSignatureHeader = None,
        origin_header: OriginHeader = None,
    ) -> ChatSuccess:
        check_environment(environment_token)
        signed = client_identity(
            client_key,
            client_signature,
            config.secret_value(config.abuse_hash_key),
        )
        hash_key = config.secret_value(config.chat_log_hash_key)
        session_value = body.conversation_id or body.visitor_id or request.state.request_id
        session_id = pseudonymize(session_value, hash_key, "conversation")
        visitor_id = pseudonymize(body.visitor_id, hash_key, "visitor") if body.visitor_id else None
        # Unsigned callers share one fail-closed bucket. User-controlled conversation and visitor
        # identifiers must never mint fresh abuse identities.
        rate_key = signed.rate_key
        await services.chat_limiter.check(rate_key)
        safety_identifier = hashlib.sha256(rate_key.encode()).hexdigest()
        return await brain.answer(
            body,
            TurnIdentity(
                request_id=request.state.request_id,
                session_id=session_id,
                visitor_id=visitor_id,
                safety_identifier=safety_identifier,
                question_origin=body.question_origin or origin_header or "client",
            ),
        )

    @app.post(
        "/v1/feedback",
        response_model=FeedbackSuccess,
        response_model_exclude_none=True,
    )
    async def feedback(
        body: FeedbackRequest,
        environment_token: EnvironmentHeader = None,
    ) -> FeedbackSuccess:
        check_environment(environment_token)
        # Feedback has no authenticated abuse identity in the frozen API, so use a stable shared
        # bucket instead of the caller-selected request ID.
        await services.feedback_limiter.check("untrusted:feedback")
        await repo.upsert_feedback(body)
        return FeedbackSuccess()

    if config.admin_enabled:

        @app.get("/v1/admin/logs", response_model_exclude_none=True)
        async def list_logs(
            authorization: AuthorizationHeader = None,
            environment_token: EnvironmentHeader = None,
            search: Annotated[str | None, Query(max_length=200)] = None,
            route: Annotated[str | None, Query(max_length=300)] = None,
            origin: Annotated[str | None, Query(max_length=64)] = None,
            version: Annotated[str | None, Query(max_length=256)] = None,
            limit: Annotated[int, Query(ge=1, le=100)] = 100,
            if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
        ) -> Response:
            check_environment(environment_token)
            check_admin(authorization)
            current = repo.version()
            if if_none_match is not None and if_none_match.strip('"') == current:
                return Response(status_code=304, headers={"ETag": f'"{current}"'})
            if version == current:
                return _json(UnmodifiedResponse(), headers={"ETag": f'"{current}"'})
            routes = {item for item in (route or "").split(",") if item}
            origins = {item for item in (origin or "").split(",") if item}
            if not origins.issubset({"client", "dev", "bot"}):
                raise ServiceError(400, "INVALID_REQUEST", "origin contains an invalid value.")
            result: LogListResponse = await repo.list_logs(
                search=search,
                routes=routes,
                origins=origins,
                limit=limit,
            )
            return _json(result, headers={"ETag": f'"{result.version}"'})

        @app.post(
            "/v1/admin/logs/feedback",
            response_model=FeedbackSuccess,
            response_model_exclude_none=True,
        )
        async def operator_feedback(
            body: OperatorFeedbackRequest,
            authorization: AuthorizationHeader = None,
            environment_token: EnvironmentHeader = None,
        ) -> FeedbackSuccess:
            check_environment(environment_token)
            check_admin(authorization)
            found = await repo.set_operator_feedback(body.log_id, body.feedback)
            if not found:
                raise ServiceError(404, "NOT_FOUND", "The log entry was not found.")
            return FeedbackSuccess()

        @app.get("/v1/admin/logs/stream")
        async def log_stream(
            authorization: AuthorizationHeader = None,
            environment_token: EnvironmentHeader = None,
        ) -> StreamingResponse:
            check_environment(environment_token)
            check_admin(authorization)
            return StreamingResponse(
                repo.changes(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    return app


app = create_app()
