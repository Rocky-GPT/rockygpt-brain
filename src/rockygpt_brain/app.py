"""Frozen FastAPI compatibility surface for Hybrid V1."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, AsyncIterator, Literal

from fastapi import FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import StringConstraints
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
from rockygpt_brain.persistence import InMemoryRepository, Repository
from rockygpt_brain.security import (
    SlidingWindowRateLimiter,
    client_identity,
    pseudonymize,
    require_admin_bearer,
    require_shared_token,
)


ClientKeyHeader = Annotated[
    str | None,
    Header(alias="x-rockygpt-client-key", max_length=512),
]
ClientSignatureHeader = Annotated[
    str | None,
    Header(alias="x-rockygpt-client-signature", max_length=512),
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


def _json(model: object, status_code: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
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
            requestId=request_id,
            error=ErrorDetail(
                code=error.code,
                message=error.public_message,
                retryable=error.retryable,
                retryAfterSeconds=error.retry_after_seconds,
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
    # Tests/local callers inject InMemoryRepository. A configured deployed app is replaced below
    # by PostgresRepository once its connection is available; never infer another service's schema.
    repo = repository or InMemoryRepository(
        recent_turn_limit=config.memory_recent_turns,
        claim_limit=config.memory_claims,
        text_retention_days=config.text_retention_days,
        metadata_retention_days=config.metadata_retention_days,
    )
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
        chat_limiter=SlidingWindowRateLimiter(
            config.chat_rate_limit, config.rate_window_seconds
        ),
        feedback_limiter=SlidingWindowRateLimiter(
            config.feedback_rate_limit, config.rate_window_seconds
        ),
    )
    started = time.monotonic()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        initialize = getattr(repo, "initialize", None)
        if initialize is not None:
            await initialize()
        await repo.purge_expired()

        async def retention_loop() -> None:
            while True:
                await asyncio.sleep(3600)
                await repo.purge_expired()

        retention_task = asyncio.create_task(retention_loop())
        try:
            yield
        finally:
            retention_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retention_task
            for dependency in (data_port, repo):
                close = getattr(dependency, "close", None)
                if close is not None:
                    await close()

    app = FastAPI(
        title="RockyGPT Hybrid V1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.services = services

    @app.middleware("http")
    async def request_boundary(request: Request, call_next: object) -> Response:
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
        response = await call_next(request)  # type: ignore[operator]
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
        message = "The requested resource was not found."
        return _error_response(
            request.state.request_id,
            ServiceError(404, "NOT_FOUND", message, retryable=False),
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
            timestamp=datetime.now(timezone.utc),
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
        visitor_id = (
            pseudonymize(body.visitor_id, hash_key, "visitor") if body.visitor_id else None
        )
        rate_key = signed.rate_key if signed.trusted else visitor_id or session_id
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
        rate_key = pseudonymize(
            body.request_id,
            config.secret_value(config.chat_log_hash_key),
            "feedback-rate",
        )
        await services.feedback_limiter.check(rate_key)
        await repo.upsert_feedback(body)
        return FeedbackSuccess()

    if config.admin_enabled:

        @app.get("/v1/admin/logs", response_model_exclude_none=True)
        async def list_logs(
            authorization: AuthorizationHeader = None,
            environment_token: EnvironmentHeader = None,
            search: Annotated[
                str | None, Query(max_length=200)
            ] = None,
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
