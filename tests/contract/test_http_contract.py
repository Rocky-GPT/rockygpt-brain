from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from conftest import FakeData, ScriptedModel
from rockygpt_brain.app import create_app
from rockygpt_brain.config import Settings
from rockygpt_brain.main import app as entrypoint_app
from rockygpt_brain.persistence import InMemoryRepository


@pytest.mark.asyncio
async def test_runtime_route_surface_and_framework_docs_are_disabled(settings: Settings) -> None:
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=ScriptedModel(),
        repository=InMemoryRepository(),
    )
    paths = {getattr(route, "path", None) for route in app.routes}
    assert paths == {
        "/health",
        "/readiness",
        "/v1/chat",
        "/v1/feedback",
        "/v1/admin/logs",
        "/v1/admin/logs/feedback",
        "/v1/admin/logs/stream",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.get("/docs")
        wrong_method = await client.get("/v1/chat")
    assert response.status_code == 404
    assert response.headers["x-request-id"] == response.json()["requestId"]
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "INVALID_REQUEST"
    assert wrong_method.headers["x-request-id"] == wrong_method.json()["requestId"]


def test_console_module_exports_the_launcher_asgi_app() -> None:
    assert entrypoint_app is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [b"null", b"[]", b'"hello"', b"{", json.dumps({"message": "hi", "extra": 1}).encode()],
)
async def test_bad_json_scalar_and_unknown_fields_are_400_without_model(
    settings: Settings, content: bytes
) -> None:
    model = ScriptedModel()
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=model,
        repository=InMemoryRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post(
            "/v1/chat", content=content, headers={"content-type": "application/json"}
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert response.headers["x-request-id"] == response.json()["requestId"]
    assert model.understand_calls == []


@pytest.mark.asyncio
async def test_body_over_64_kib_is_413(settings: Settings) -> None:
    model = ScriptedModel()
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=model,
        repository=InMemoryRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post(
            "/v1/chat",
            content=b"x" * 65_537,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert model.understand_calls == []


@pytest.mark.asyncio
async def test_feedback_upserts_and_admin_log_contract(settings: Settings) -> None:
    repo = InMemoryRepository()
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=ScriptedModel(),
        repository=repo,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        chat = await client.post("/v1/chat", json={"message": "Is SGA overrated?"})
        request_id = chat.json()["requestId"]
        one = await client.post("/v1/feedback", json={"requestId": request_id, "rating": -1})
        two = await client.post(
            "/v1/feedback",
            json={
                "requestId": request_id,
                "rating": 1,
                "category": "other",
                "comments": "email me at person@example.com",
            },
        )
        logs = await client.get(
            "/v1/admin/logs", headers={"authorization": "Bearer admin-token-value"}
        )
    assert one.json() == {"success": True}
    assert two.json() == {"success": True}
    assert logs.status_code == 200
    item = logs.json()["logs"][0]
    assert item["feedback_rating"] == 1
    assert item["feedback_comment"] == "email me at [EMAIL]"
    assert not item["session_id"].endswith(request_id)


@pytest.mark.asyncio
async def test_malformed_signed_identity_falls_back_without_rejecting_chat(
    settings: Settings,
) -> None:
    repo = InMemoryRepository()
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=ScriptedModel(),
        repository=repo,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post(
            "/v1/chat",
            json={"message": "Is SGA overrated?", "visitorId": "visitor-safe"},
            headers={
                "x-rockygpt-client-key": "not a valid signed key!",
                "x-rockygpt-client-signature": "not-hex",
            },
        )
        overlong = await client.post(
            "/v1/chat",
            json={"message": "Is SGA overrated?", "visitorId": "visitor-safe"},
            headers={
                "x-rockygpt-client-key": "k" * 513,
                "x-rockygpt-client-signature": "s" * 513,
            },
        )
    assert response.status_code == 200
    assert overlong.status_code == 200
    logs = await repo.list_logs(search=None, routes=set(), origins=set(), limit=10)
    serialized = logs.model_dump_json()
    assert "not a valid signed key" not in serialized
    assert "not-hex" not in serialized
    assert "k" * 513 not in serialized
    assert "s" * 513 not in serialized


class FiniteStreamRepository(InMemoryRepository):
    async def changes(self) -> AsyncIterator[str]:
        yield 'data: {"type":"change"}\n\n'


@pytest.mark.asyncio
async def test_sse_handshake_has_request_id(settings: Settings) -> None:
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=ScriptedModel(),
        repository=FiniteStreamRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.get(
            "/v1/admin/logs/stream",
            headers={"authorization": "Bearer admin-token-value"},
        )

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == 'data: {"type":"change"}\n\n'


@pytest.mark.asyncio
async def test_missing_database_fails_closed_and_readiness_is_unready() -> None:
    config = Settings(
        app_env="test",
        openai_api_key="test-key",
        chat_log_hash_key="c" * 32,
        abuse_hash_key="a" * 32,
        database_url="",
    )
    app = create_app(settings=config, data=FakeData(), model=ScriptedModel())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        ready = await client.get("/readiness")
        chat = await client.post("/v1/chat", json={"message": "Is SGA overrated?"})
    assert ready.status_code == 503
    assert "database" in ready.json()["failing"]
    assert chat.status_code == 503
    assert chat.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
