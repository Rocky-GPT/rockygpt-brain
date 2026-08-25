"""FastAPI surface for the BASE hybrid brain."""

from __future__ import annotations

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

from rockygpt_brain.api.contracts import (
    ChatRequest,
    ChatSuccess,
    ErrorDetail,
    ErrorResponse,
    FeedbackRequest,
    FeedbackSuccess,
    Health,
    OperatorFeedbackRequest,
    Readiness,
    UnmodifiedResponse,
)
from rockygpt_brain.brain.brain import Brain, TurnIdentity
from rockygpt_brain.brain.plan.run import OpenAIPlan, PlanPort
from rockygpt_brain.brain.understand.run import OpenAIUnderstand, UnderstandPort
from rockygpt_brain.brain.write.run import OpenAIWrite, WritePort
from rockygpt_brain.config import Settings, get_settings
from rockygpt_brain.context.memory import MemoryStore
from rockygpt_brain.errors import ServiceError
from rockygpt_brain.services.data import DataPort, HttpData
from rockygpt_brain.services.web import OpenAIWeb, WebPort

EnvironmentHeader = Annotated[
    str | None,
    Header(alias="x-rockygpt-environment-token"),
]
AuthorizationHeader = Annotated[str | None, Header(alias="authorization")]
OriginHeader = Annotated[
    Literal["client", "dev", "bot"] | None,
    Header(alias="x-rockygpt-origin"),
]


@dataclass(slots=True)
class AppServices:
    model: WritePort
    planner: PlanPort
    data: DataPort
    web: WebPort
    memory: MemoryStore
    brain: Brain


def _json(
    value: object, status_code: int = 200, headers: dict[str, str] | None = None
) -> JSONResponse:
    content = (
        value.model_dump(mode="json", by_alias=True, exclude_none=True)
        if hasattr(value, "model_dump")
        else value
    )
    return JSONResponse(content=content, status_code=status_code, headers=headers)


def _error(request_id: str, error: ServiceError) -> JSONResponse:
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
        error.status_code,
        {"X-Request-Id": request_id},
    )


def create_app(
    *,
    settings: Settings | None = None,
    model: WritePort | None = None,
    understand: UnderstandPort | None = None,
    planner: PlanPort | None = None,
    data: DataPort | None = None,
    web: WebPort | None = None,
    memory: MemoryStore | None = None,
) -> FastAPI:
    config = settings or get_settings()
    model_port = model or OpenAIWrite(
        config.secret_value(config.openai_api_key),
        config.openai_chat_model,
    )
    understand_port = understand or OpenAIUnderstand(
        config.secret_value(config.openai_api_key),
        config.openai_planner_model,
    )
    planner_port = planner or OpenAIPlan(
        config.secret_value(config.openai_api_key),
        config.openai_planner_model,
    )
    data_port = data or HttpData(config.data_url, config.data_timeout_seconds)
    web_port = web or OpenAIWeb(
        config.secret_value(config.openai_api_key),
        config.openai_web_model,
    )
    memory_store = memory or MemoryStore()
    brain = Brain(
        model_port,
        understand_port,
        planner_port,
        data_port,
        web_port,
        memory_store,
        config.campus_timezone,
    )
    services = AppServices(model_port, planner_port, data_port, web_port, memory_store, brain)
    started = time.monotonic()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(
        title="RockyGPT BASE",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.services = services

    @app.middleware("http")
    async def add_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return _error(request.state.request_id, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _: RequestValidationError) -> JSONResponse:
        return _error(
            request.state.request_id,
            ServiceError(400, "INVALID_REQUEST", "The request is invalid."),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _: Exception) -> JSONResponse:
        return _error(
            request.state.request_id,
            ServiceError(500, "INTERNAL_ERROR", "An unexpected error occurred."),
        )

    def check_environment(token: str | None) -> None:
        expected = config.secret_value(config.staging_service_token)
        if expected and token != expected:
            raise ServiceError(401, "UNAUTHORIZED", "Unauthorized.")

    def check_admin(authorization: str | None) -> None:
        expected = config.secret_value(config.admin_api_token)
        if expected and authorization != f"Bearer {expected}":
            raise ServiceError(401, "UNAUTHORIZED", "Unauthorized.")

    @app.get("/health", response_model=Health, response_model_exclude_none=True)
    async def health() -> Health:
        return Health(
            status="healthy",
            service="rockygpt-brain",
            uptimeSeconds=time.monotonic() - started,
        )

    @app.get("/readiness")
    async def readiness() -> Response:
        failing: list[str] = []
        if not model_port.configured:
            failing.append("model")
        if not planner_port.configured:
            failing.append("planner")
        result = Readiness(
            status="unready" if failing else "ready",
            failing=failing or None,
            timestamp=datetime.now(UTC),
        )
        return _json(result, 503 if failing else 200)

    @app.post("/v1/chat", response_model=ChatSuccess, response_model_by_alias=True)
    async def chat(
        request: Request,
        body: ChatRequest,
        environment_token: EnvironmentHeader = None,
        origin_header: OriginHeader = None,
    ) -> ChatSuccess:
        check_environment(environment_token)
        session_id = body.conversation_id or body.visitor_id or request.state.request_id
        return await brain.answer(
            body,
            TurnIdentity(
                request_id=request.state.request_id,
                session_id=session_id,
                visitor_id=body.visitor_id,
                question_origin=body.question_origin or origin_header or "client",
            ),
        )

    @app.post("/v1/feedback", response_model=FeedbackSuccess)
    async def feedback(
        body: FeedbackRequest,
        environment_token: EnvironmentHeader = None,
    ) -> FeedbackSuccess:
        check_environment(environment_token)
        memory_store.save_feedback(body)
        return FeedbackSuccess()

    if config.admin_enabled:

        @app.get("/v1/admin/logs")
        async def list_logs(
            authorization: AuthorizationHeader = None,
            environment_token: EnvironmentHeader = None,
            search: Annotated[str | None, Query()] = None,
            route: Annotated[str | None, Query()] = None,
            origin: Annotated[str | None, Query()] = None,
            version: Annotated[str | None, Query()] = None,
            limit: Annotated[int, Query(ge=1, le=100)] = 100,
            if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
        ) -> Response:
            check_environment(environment_token)
            check_admin(authorization)
            current = memory_store.version
            if if_none_match and if_none_match.strip('"') == current:
                return Response(status_code=304, headers={"ETag": f'"{current}"'})
            if version == current:
                return _json(UnmodifiedResponse(), headers={"ETag": f'"{current}"'})
            result = memory_store.list_logs(
                search=search,
                routes=set(filter(None, (route or "").split(","))),
                origins=set(filter(None, (origin or "").split(","))),
                limit=limit,
            )
            return _json(result, headers={"ETag": f'"{result.version}"'})

        @app.post("/v1/admin/logs/feedback", response_model=FeedbackSuccess)
        async def operator_feedback(
            body: OperatorFeedbackRequest,
            authorization: AuthorizationHeader = None,
            environment_token: EnvironmentHeader = None,
        ) -> FeedbackSuccess:
            check_environment(environment_token)
            check_admin(authorization)
            memory_store.set_operator_feedback(body.log_id, body.feedback)
            return FeedbackSuccess()

        @app.get("/v1/admin/logs/stream")
        async def log_stream(
            authorization: AuthorizationHeader = None,
            environment_token: EnvironmentHeader = None,
        ) -> StreamingResponse:
            check_environment(environment_token)
            check_admin(authorization)
            return StreamingResponse(memory_store.changes(), media_type="text/event-stream")

    return app


app = create_app()
