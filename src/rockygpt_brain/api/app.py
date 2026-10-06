"""HTTP admission and the single bounded chat path."""

import asyncio
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from typing import Annotated

from fastapi import FastAPI, Header, Query, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from rockygpt_brain.api.dev import add_dev_routes
from rockygpt_brain.boundary import check
from rockygpt_brain.contract import CONVERSATION_ID_HEADER, CONVERSATION_ID_PATTERN, ChatRequest
from rockygpt_brain.engine import HELP_SECONDS, ChatEngine, ChatResult, answered, failed
from rockygpt_brain.failures import failure
from rockygpt_brain.provider import Gateway, GatewayError
from rockygpt_brain.retrieval import (
    DatasetChanged,
    EvidenceUnavailable,
    InvalidFactRequest,
    PostgresEntityFacts,
    UnknownEntity,
)
from rockygpt_brain.settings import ConfigurationError, ProviderSettings
from rockygpt_brain.spending import SpendingError
from rockygpt_brain.timing import measure, request_timing
from rockygpt_brain.turn import Turn, intake, new_id

MAX_BODY_BYTES = 64 * 1_024
BODY_SECONDS = 10
LOG = logging.getLogger(__name__)
INVALID = "The request isn't in the shape RockyGPT expects."
# The dev UI asks for the turn's trace and metrics with this header. Never honored in production.
DEBUG_HEADER = "x-rockygpt-diagnostics"


class Admission:
    """Authenticate server ingress and bound bytes before JSON decoding or paid work."""

    def __init__(self, app: ASGIApp, *, token: str | None, environment: str) -> None:
        self.app, self.token, self.environment = app, token, environment

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        diagnostic = (scope["type"] == "http" and scope["path"] == "/v1/chat"
                      and scope["method"] == "POST" and self.environment == "development"
                      and Headers(scope=scope).get(DEBUG_HEADER) == "1")
        if not diagnostic:
            await self._admit(scope, receive, send)
            return
        # Chat returns one JSON document. Record the whole admission/engine path,
        # including early refusals, before attaching diagnostics to that document.
        with request_timing() as timeline:
            start: Message | None = None
            chunks: list[bytes] = []

            async def timed_send(event: Message) -> None:
                nonlocal start
                if event["type"] == "http.response.start":
                    start = event
                    return
                if event["type"] != "http.response.body" or start is None:
                    await send(event)
                    return
                chunks.append(event.get("body", b""))
                if event.get("more_body", False):
                    return
                body = json.loads(b"".join(chunks))
                body["metrics"] = {**body.get("metrics", {}), "timing": timeline.report()}
                encoded = JSONResponse(body).body
                headers = [(key, value) for key, value in start["headers"]
                           if key.lower() not in (b"content-length", b"x-rockygpt-brain-total-us")]
                headers.extend([
                    (b"content-length", str(len(encoded)).encode()),
                    # Includes diagnostic assembly and final JSON encoding. The UI
                    # accounts for that tail separately from the recorded spans.
                    (b"x-rockygpt-brain-total-us", str(timeline.elapsed_us()).encode()),
                ])
                await send({**start, "headers": headers})
                await send({"type": "http.response.body", "body": encoded})

            await self._admit(scope, receive, timed_send)

    async def _admit(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            await self.app(scope, receive, send)
            return
        if self.environment == "production" and not self.token:
            await failure(503, "service_not_configured", "RockyGPT isn't configured to serve.",
                          new_id())(scope, receive, send)
            return
        if self.token and not secrets.compare_digest(
            Headers(scope=scope).get("x-rockygpt-environment-token", "").encode(),
            self.token.encode(),
        ):
            await failure(401, "unauthorized", "This request isn't authorized.", new_id())(
                scope, receive, send)
            return
        if scope["path"] != "/v1/chat" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        try:
            async with asyncio.timeout(BODY_SECONDS):
                while True:
                    with measure("Read request body"):
                        event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    body.extend(event.get("body", b""))
                    if len(body) > MAX_BODY_BYTES:
                        await failure(413, "request_too_large", "This request is too large.",
                                      new_id())(scope, receive, send)
                        return
                    if not event.get("more_body", False):
                        break
        except TimeoutError:
            await failure(408, "request_timeout", "The request body arrived too slowly.",
                          new_id())(scope, receive, send)
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _configured_engine() -> ChatEngine:
    settings = ProviderSettings.from_env()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigurationError("Missing DATABASE_URL")
    return ChatEngine(Gateway(settings), PostgresEntityFacts(database_url),
                      max_turn_nusd=settings.max_turn_nusd)


def create_app(engine: ChatEngine | None = None, *, service_token: str | None = None,
               environment: str | None = None, max_concurrent: int = 8,
               clock: Callable[[], datetime] | None = None) -> FastAPI:
    if max_concurrent < 1:
        raise ValueError("max_concurrent must be positive")
    active_environment = environment or os.getenv("BRAIN_ENVIRONMENT", "development")
    if active_environment not in ("development", "production"):
        raise ValueError("Invalid Brain environment")
    token = service_token if service_token is not None else os.getenv("STAGING_SERVICE_TOKEN")
    token = token.strip() if token else None

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active = engine
        try:
            if active is None:
                active = _configured_engine()
            application.state.engine = active
            if isinstance(active.gateway, Gateway):
                await active.gateway.open()
        except ConfigurationError:
            application.state.startup_error = "model_not_configured"
        except SpendingError:
            application.state.startup_error = "ledger_unavailable"
        try:
            yield
        finally:
            if active is not None and isinstance(active.gateway, Gateway):
                await active.gateway.close()

    application = FastAPI(title="RockyGPT Brain", lifespan=lifespan)
    application.state.engine = engine
    application.state.startup_error = None
    application.state.capacity = asyncio.Semaphore(max_concurrent)
    application.add_middleware(Admission, token=token, environment=active_environment)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # User-controlled extra field names can contain secrets too; don't echo those keys.
        known = {"body", "header", "query", "path", "messages", "role", "content",
                 "omittedMessages", "dataset_version", "identity_hash", "entity_id",
                 CONVERSATION_ID_HEADER}
        detail = [{"type": e["type"],
                   "loc": [p if isinstance(p, int) or p in known else "unknown_field"
                           for p in e["loc"]],
                   "msg": "The request field is invalid."} for e in exc.errors()]
        return failure(422, "invalid_request", INVALID, new_id(), detail=detail)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 400:
            detail = [{"type": "body_unreadable", "loc": ["body"], "msg": "Unreadable body."}]
            return failure(422, "invalid_request", INVALID, new_id(), detail=detail)
        return await http_exception_handler(request, exc)  # type: ignore[return-value]

    @application.api_route("/health", methods=["GET", "HEAD"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.api_route("/readiness", methods=["GET", "HEAD"])
    async def readiness() -> JSONResponse:
        active = application.state.engine
        ready = (not application.state.startup_error and active is not None
                 and (active_environment != "production" or bool(token))
                 and await active.readiness())
        return JSONResponse({"status": "ready" if ready else "not_ready"},
                            status_code=200 if ready else 503)

    @application.post("/v1/chat")
    async def chat(
        request: ChatRequest, transport: Request,
        conversation_id: Annotated[
            str | None, Header(alias=CONVERSATION_ID_HEADER, pattern=CONVERSATION_ID_PATTERN)
        ] = None,
    ) -> JSONResponse:
        with measure("Prepare turn and check safety boundary"):
            turn = (intake(request, conversation_id, now=clock()) if clock
                    else intake(request, conversation_id))
            boundary = check(turn)
        if boundary.kind == "safety":
            floor = application.state.engine
            if floor is None or application.state.startup_error:
                result = replace(answered(turn, boundary.message, "partial"), trace={
                    "decidedBy": "phrase_floor", "modelCalls": 0, "situation": boundary.situation})
            else:  # The emergency text, then the campus numbers if they can be read in time.
                result = await floor.safety_reply(turn, boundary.situation)
        elif application.state.engine is None or application.state.startup_error:
            result = failed(turn, application.state.startup_error or "model_not_configured")
        else:
            capacity = application.state.capacity
            if capacity.locked():
                return failure(429, "busy", "RockyGPT is busy. Please try again shortly.",
                               turn.request_id, retryable=True)
            async with capacity:
                result = await _connected_answer(application.state.engine, turn, request, transport)
        body = result.body
        if active_environment == "development" and transport.headers.get(DEBUG_HEADER) == "1":
            if result.trace is None:  # The turn failed outside the engine, so nothing was kept.
                body = {**body, "metrics": {"decidedBy": "error"}}
            else:
                trace = result.trace
                body = {**body, "trace": trace.get("lookups", []),
                        "metrics": {k: v for k, v in trace.items() if k != "lookups"}}
        return JSONResponse(body, status_code=result.status_code,
                            headers={"X-Request-Id": turn.request_id, "Cache-Control": "no-store"})

    @application.get("/v1/entities/{entity_id}/facts")
    async def entity_facts(
        entity_id: str,
        dataset_version: Annotated[str, Query(min_length=1, max_length=256)],
        identity_hash: Annotated[str, Query(min_length=1, max_length=128)],
    ) -> JSONResponse:
        active = application.state.engine
        if active is None:
            return failure(503, "data_unavailable", "Campus data is unavailable.", new_id())
        try:
            async with asyncio.timeout(5):
                facts = await asyncio.to_thread(
                    active.facts.get_office_facts, entity_id, None, dataset_version,
                    identity_hash=identity_hash)
            return JSONResponse(facts, headers={"Cache-Control": "no-store"})
        except DatasetChanged:
            return failure(409, "dataset_changed", "Published data changed. Reload it.", new_id())
        except UnknownEntity:
            return failure(404, "unknown_entity", "Office not found in this publication.", new_id())
        except InvalidFactRequest:
            return failure(422, "invalid_request", INVALID, new_id())
        except (EvidenceUnavailable, TimeoutError):
            return failure(503, "data_unavailable", "Campus data is unavailable.", new_id(),
                           retryable=True)

    if active_environment == "development":
        add_dev_routes(application)

    return application


async def _connected_answer(engine: ChatEngine, turn: Turn, request: ChatRequest,
                            transport: Request) -> ChatResult:
    async def disconnected() -> None:
        while True:
            if (await transport.receive())["type"] == "http.disconnect":
                return

    work = asyncio.create_task(engine.answer(turn, request))
    gone = asyncio.create_task(disconnected())
    try:
        async with asyncio.timeout(engine.turn_seconds + HELP_SECONDS + 2):
            done, _ = await asyncio.wait((work, gone), return_when=asyncio.FIRST_COMPLETED)
            if gone in done:
                return ChatResult(499, {"requestId": turn.request_id, "reason": "cancelled"})
            return await work
    except TimeoutError:
        return failed(turn, "model_timeout")
    except GatewayError as error:
        return failed(turn, error.code)
    except EvidenceUnavailable:
        return failed(turn, "data_unavailable")
    except Exception as error:
        # Exception messages can contain provider payloads or student data.
        LOG.error("brain_failure request_id=%s exception_type=%s",
                  turn.request_id, type(error).__name__)
        return failed(turn, "internal_error")
    finally:
        for task in (work, gone):
            task.cancel()
        await asyncio.gather(work, gone, return_exceptions=True)


app = create_app()
