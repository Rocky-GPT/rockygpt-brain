from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

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
from rockygpt_brain.capabilities.registry import CAPABILITIES, catalogue
from rockygpt_brain.config import Settings, get_settings
from rockygpt_brain.context.memory import MemoryStore
from rockygpt_brain.context.postgres_logs import PostgresLogStore
from rockygpt_brain.errors import (
    BadRequest,
    DatasetUnavailable,
    Internal,
    ServiceError,
    Unauthorized,
)
from rockygpt_brain.lanes.code.run import project
from rockygpt_brain.services.data import DataPort, DataUnavailable, HttpData
from rockygpt_brain.services.rag.client import HttpRag, RagPort
from rockygpt_brain.services.web import OpenAIWeb, WebPort

AuthorizationHeader = Annotated[str | None, Header(alias="authorization")]
OriginHeader = Annotated[
    Literal["client", "dev", "bot"] | None,
    Header(alias="x-rockygpt-origin"),
]

logger = logging.getLogger(__name__)

_DEVELOPMENT_LOG_HASH_KEY = "rockygpt-development-only-hash-key"


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
    documents: RagPort | None = None,
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
    documents_port = documents or HttpRag(config.data_url, config.data_timeout_seconds)
    web_port = web or OpenAIWeb(
        config.secret_value(config.openai_api_key),
        config.openai_web_model,
    )
    durable_logs: PostgresLogStore | None = None
    if memory is not None:
        memory_store = memory
    else:
        database_url = config.secret_value(config.database_url)
        durable_logs = (
            PostgresLogStore(
                database_url,
                hash_key=(
                    config.secret_value(config.chat_log_hash_key) or _DEVELOPMENT_LOG_HASH_KEY
                ),
            )
            if database_url
            else None
        )
        memory_store = MemoryStore(durable_logs)
    brain = Brain(
        model_port,
        understand_port,
        planner_port,
        data_port,
        web_port,
        documents_port,
        memory_store,
        config.campus_timezone,
        config.rag_enabled,
    )
    services = AppServices(model_port, planner_port, data_port, web_port, memory_store, brain)
    started = time.monotonic()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        # Open the log pool before serving, not on the first turn that needs it.
        # Lazily, the first write also pays for a TLS handshake to Neon and the
        # schema DDL — seconds of work, on the one write most likely to be
        # interrupted, since a cold container is a container about to idle out.
        if durable_logs is not None:
            try:
                await durable_logs.initialize()
            except Exception:
                logger.exception("Chat logs are unavailable; answers will not be recorded")
        try:
            yield
        finally:
            await memory_store.close()

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
            BadRequest("The request is invalid."),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, _: Exception) -> JSONResponse:
        return _error(
            request.state.request_id,
            Internal("An unexpected error occurred."),
        )

    def check_admin(authorization: str | None) -> None:
        expected = config.secret_value(config.admin_api_token)
        if expected and authorization != f"Bearer {expected}":
            raise Unauthorized("Unauthorized.")

    @app.get("/health", response_model=Health, response_model_exclude_none=True)
    async def health() -> Health:
        return Health(
            status="healthy",
            service="rockygpt-brain",
            uptimeSeconds=time.monotonic() - started,
        )

    def chat_logs_degraded() -> bool:
        # Only meaningful where durable logs were asked for. With no
        # DATABASE_URL the service is keeping logs in memory by choice, which
        # is not a fault to report.
        return durable_logs is not None and not durable_logs.connected

    @app.get("/readiness")
    async def readiness() -> Response:
        failing: list[str] = []
        if not model_port.configured:
            failing.append("model")
        if not planner_port.configured:
            failing.append("planner")
        degraded = ["chat-logs"] if chat_logs_degraded() else []
        result = Readiness(
            status="unready" if failing else "ready",
            failing=failing or None,
            degraded=degraded or None,
            timestamp=datetime.now(UTC),
        )
        # Degradation deliberately does not change the status code. Rocky can
        # still answer every question with its log store down, and the UI
        # reads a non-2xx here as the whole deployment being unavailable.
        return _json(result, 503 if failing else 200)

    @app.get("/readiness/chat-logs")
    async def chat_logs_readiness() -> Response:
        """A 503 an uptime monitor can be pointed at.

        Nothing else consumes this endpoint, so it can fail loudly without
        taking the site down with it. Watch it externally: persistence
        failures are swallowed by design, so a silent log store is otherwise
        invisible until someone opens the dashboard and notices the gap.
        """
        broken = chat_logs_degraded()
        result = Readiness(
            status="unready" if broken else "ready",
            failing=["chat-logs"] if broken else None,
            timestamp=datetime.now(UTC),
        )
        return _json(result, 503 if broken else 200)

    @app.get("/v1/capabilities")
    async def capabilities() -> Response:
        return _json({"capabilities": catalogue()}, 200)

    @app.get("/v1/capabilities/{name}/records")
    async def capability_records(name: str) -> Response:
        entry = CAPABILITIES.get(name)
        if entry is None:
            raise BadRequest(f"There is no {name!r} capability.")
        now = datetime.now(UTC).astimezone(ZoneInfo(config.campus_timezone))
        try:
            records = await entry.execute({}, now, data_port)
        except DataUnavailable as exc:
            raise DatasetUnavailable("Rocky could not reach campus data just now.") from exc
        return _json(
            {
                "capability": name,
                "returned": len(records),
                "records": [project(record, name) for record in records],
            },
            200,
        )

    @app.post("/v1/chat", response_model=ChatSuccess, response_model_by_alias=True)
    async def chat(
        request: Request,
        body: ChatRequest,
        origin_header: OriginHeader = None,
    ) -> ChatSuccess:
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
    ) -> FeedbackSuccess:
        await memory_store.save_feedback_persisted(body)
        return FeedbackSuccess()

    if config.admin_enabled:

        @app.get("/v1/admin/logs")
        async def list_logs(
            authorization: AuthorizationHeader = None,
            search: Annotated[str | None, Query()] = None,
            route: Annotated[str | None, Query()] = None,
            origin: Annotated[str | None, Query()] = None,
            version: Annotated[str | None, Query()] = None,
            limit: Annotated[int, Query(ge=1, le=100)] = 100,
            if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
        ) -> Response:
            check_admin(authorization)
            requested_version = version or (if_none_match or "").strip('"') or None
            result = await memory_store.read_logs(
                search=search,
                routes=set(filter(None, (route or "").split(","))),
                origins=set(filter(None, (origin or "").split(","))),
                limit=limit,
                version=requested_version,
            )
            if isinstance(result, UnmodifiedResponse):
                if if_none_match:
                    return Response(status_code=304)
                return _json(result)
            return _json(result, headers={"ETag": f'"{result.version}"'})

        @app.post("/v1/admin/logs/feedback", response_model=FeedbackSuccess)
        async def operator_feedback(
            body: OperatorFeedbackRequest,
            authorization: AuthorizationHeader = None,
        ) -> FeedbackSuccess:
            check_admin(authorization)
            await memory_store.set_operator_feedback_persisted(body.log_id, body.feedback)
            return FeedbackSuccess()

        @app.get("/v1/admin/logs/stream")
        async def log_stream(
            authorization: AuthorizationHeader = None,
        ) -> StreamingResponse:
            check_admin(authorization)
            return StreamingResponse(memory_store.changes(), media_type="text/event-stream")

    return app


app = create_app()
