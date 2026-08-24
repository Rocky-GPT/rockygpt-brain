from __future__ import annotations

import json

import httpx
import pytest

from conftest import FakeData, ScriptedModel
from rockygpt_brain.app import create_app
from rockygpt_brain.persistence import InMemoryRepository


@pytest.mark.asyncio
async def test_runtime_route_surface_and_framework_docs_are_disabled(settings: object) -> None:
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=ScriptedModel(),
        repository=InMemoryRepository(),
    )
    paths = {route.path for route in app.routes}
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
    assert response.status_code == 404
    assert response.headers["x-request-id"] == response.json()["requestId"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [b"null", b"[]", b'"hello"', b"{", json.dumps({"message": "hi", "extra": 1}).encode()],
)
async def test_bad_json_scalar_and_unknown_fields_are_400_without_model(
    settings: object, content: bytes
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
async def test_body_over_64_kib_is_413(settings: object) -> None:
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
async def test_feedback_upserts_and_admin_log_contract(settings: object) -> None:
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
        one = await client.post(
            "/v1/feedback", json={"requestId": request_id, "rating": -1}
        )
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
