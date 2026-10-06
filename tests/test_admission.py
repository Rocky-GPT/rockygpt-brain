"""Admission and cancellation across the HTTP boundary, without paid calls."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from rockygpt_brain.api import app as app_module
from rockygpt_brain.api.app import Admission, create_app
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine, ChatResult, answered
from rockygpt_brain.provider import GatewayError
from rockygpt_brain.turn import Turn

QUESTION = {"messages": [{"role": "user", "content": "What time is it?"}]}


class BoundaryEngine(ChatEngine):
    def __init__(self, error: Exception | None = None, block: bool = False) -> None:
        # The boundary only needs answer/readiness, never a real provider or database.
        self.gateway = object()  # type: ignore[assignment]
        self.turn_seconds = 1.0
        self.error, self.block = error, block
        self.started, self.cancelled = asyncio.Event(), asyncio.Event()
        self.calls = 0

    async def readiness(self) -> bool:
        return True

    async def answer(self, turn: Turn, request: ChatRequest) -> ChatResult:
        self.calls += 1
        self.started.set()
        if self.error:
            raise self.error
        if self.block:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return answered(turn, "A controlled test answer.", "answered")


def test_authentication_happens_before_parsing_or_work() -> None:
    engine = BoundaryEngine()
    with TestClient(create_app(engine, service_token="fixture-secret",  # noqa: S106
                               environment="production")) as client:
        response = client.post("/v1/chat", content=b"{malformed")
        assert response.status_code == 401
        assert engine.calls == 0
        assert client.get("/v1/entities/id/facts").status_code == 401
        authorized = client.post("/v1/chat", json=QUESTION,
                                 headers={"x-rockygpt-environment-token": "fixture-secret"})
        assert authorized.status_code == 200
        assert engine.calls == 1
        assert client.get("/readiness").status_code == 200


def test_production_without_token_fails_closed_but_remains_live() -> None:
    engine = BoundaryEngine()
    with TestClient(create_app(engine, service_token="", environment="production")) as client:
        assert client.post("/v1/chat", json=QUESTION).status_code == 503
        assert client.get("/readiness").status_code == 503
        assert client.get("/health").status_code == 200
    assert engine.calls == 0


def test_oversized_body_is_rejected_before_validation_or_work() -> None:
    engine = BoundaryEngine()
    with TestClient(create_app(engine, service_token="", environment="development")) as client:
        response = client.post("/v1/chat", content=b"x" * (app_module.MAX_BODY_BYTES + 1))
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "request_too_large"
        assert response.headers["cache-control"] == "no-store"
    assert engine.calls == 0


def test_slow_body_has_a_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "BODY_SECONDS", 0.001)

    async def run() -> list[Message]:
        sent: list[Message] = []

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            pytest.fail("A timed-out request must not enter the application")

        async def receive() -> Message:
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            sent.append(message)

        scope: Scope = {"type": "http", "path": "/v1/chat", "method": "POST", "headers": []}
        await Admission(downstream, token=None, environment="development")(scope, receive, send)
        return sent

    sent = asyncio.run(run())
    assert sent[0]["status"] == 408


@pytest.mark.parametrize(("error", "expected_code"), [
    (GatewayError("ledger_unavailable"), "ledger_unavailable"),
    (RuntimeError("private student text and credential"), "internal_error"),
])
def test_cleanup_does_not_replace_a_safe_failure(error: Exception, expected_code: str,
                                                caplog: pytest.LogCaptureFixture) -> None:
    with TestClient(create_app(BoundaryEngine(error), service_token="",
                               environment="development")) as client:
        response = client.post("/v1/chat", json=QUESTION)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == expected_code
    assert "private student" not in response.text + caplog.text


def test_busy_requests_do_not_queue_and_disconnect_cancels_work() -> None:
    async def run() -> None:
        engine = BoundaryEngine(block=True)
        application = create_app(engine, service_token="", environment="development",
                                 max_concurrent=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                    base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/chat", json=QUESTION))
            await engine.started.wait()
            second = await client.post("/v1/chat", json=QUESTION)
            assert second.status_code == 429
            assert second.json()["error"]["code"] == "busy"
            assert engine.calls == 1
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            assert engine.cancelled.is_set()
            engine.block = False
            assert (await client.post("/v1/chat", json=QUESTION)).status_code == 200

    asyncio.run(run())


def test_whole_turn_deadline_stops_work() -> None:
    engine = BoundaryEngine(block=True)
    # The normal engine owns the 40s deadline; the HTTP guard also bounds a broken engine.
    engine.turn_seconds = -1.99
    with TestClient(create_app(engine, service_token="", environment="development")) as client:
        response = client.post("/v1/chat", json=QUESTION)
    assert response.status_code == 504
    assert engine.cancelled.is_set()


class TracedEngine(BoundaryEngine):
    async def answer(self, turn: Turn, request: ChatRequest) -> ChatResult:
        result = await super().answer(turn, request)
        return ChatResult(result.status_code, result.body, trace={
            "decidedBy": "model", "modelCalls": 2, "lookups": [{"tool": "office_facts"}]})


def _ask(environment: str, headers: dict[str, str]) -> dict[str, object]:
    application = create_app(TracedEngine(), service_token="t",  # noqa: S106
                             environment=environment)
    with TestClient(application) as client:
        response = client.post("/v1/chat", json=QUESTION,
                               headers={"x-rockygpt-environment-token": "t", **headers})
    assert response.status_code == 200
    return dict(response.json())


def test_the_trace_reaches_only_a_development_request_that_asks_for_it() -> None:
    shown = _ask("development", {"x-rockygpt-diagnostics": "1"})
    assert shown["trace"] == [{"tool": "office_facts"}]
    assert shown["metrics"] == {"decidedBy": "model", "modelCalls": 2}
    for body in (_ask("development", {}), _ask("development", {"x-rockygpt-diagnostics": "yes"}),
                 _ask("production", {"x-rockygpt-diagnostics": "1"})):
        assert "trace" not in body and "metrics" not in body


def test_the_phrase_floor_reports_that_it_decided() -> None:
    engine = TracedEngine()
    with TestClient(create_app(engine, service_token="", environment="development")) as client:
        body = client.post(
            "/v1/chat", headers={"x-rockygpt-diagnostics": "1"},
            json={"messages": [{"role": "user",
                                "content": "my roommate just collapsed and isnt breathing"}]},
        ).json()
    assert body["metrics"] == {"decidedBy": "phrase_floor", "modelCalls": 0}
    assert body["trace"] == [] and engine.calls == 0
