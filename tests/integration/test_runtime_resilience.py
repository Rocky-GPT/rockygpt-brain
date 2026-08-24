from __future__ import annotations

import httpx
import pytest

from conftest import FakeData, ScriptedModel
from rockygpt_brain.app import create_app
from rockygpt_brain.config import Settings
from rockygpt_brain.memory import MemorySnapshot
from rockygpt_brain.persistence import InMemoryRepository
from rockygpt_brain.security import SlidingWindowRateLimiter


class StartupUnavailableRepository(InMemoryRepository):
    async def initialize(self) -> None:
        raise OSError("database is unavailable during startup")

    async def readiness(self) -> bool:
        return False


class RuntimeUnavailableRepository(InMemoryRepository):
    async def load_memory(self, session_id: str) -> MemorySnapshot:
        del session_id
        raise OSError("database connection was lost")


@pytest.mark.asyncio
async def test_database_startup_failure_keeps_health_and_readiness_available(
    settings: Settings,
) -> None:
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=ScriptedModel(),
        repository=StartupUnavailableRepository(),
    )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://brain"
        ) as client:
            health = await client.get("/health")
            readiness = await client.get("/readiness")

    assert health.status_code == 200
    assert readiness.status_code == 503
    assert "database" in readiness.json()["failing"]


@pytest.mark.asyncio
async def test_runtime_repository_failure_maps_to_safe_503(settings: Settings) -> None:
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=ScriptedModel(),
        repository=RuntimeUnavailableRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post("/v1/chat", json={"message": "Is SGA overrated?"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert response.json()["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_unsigned_chat_cannot_rotate_caller_ids_to_bypass_limit(
    settings: Settings,
) -> None:
    limited = settings.model_copy(update={"chat_rate_limit": 1})
    model = ScriptedModel()
    app = create_app(
        settings=limited,
        data=FakeData(),
        model=model,
        repository=InMemoryRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        first = await client.post(
            "/v1/chat",
            json={
                "message": "Is SGA overrated?",
                "conversationId": "conversation-one",
                "visitorId": "visitor-one",
            },
        )
        second = await client.post(
            "/v1/chat",
            json={
                "message": "Is SGA overrated?",
                "conversationId": "conversation-two",
                "visitorId": "visitor-two",
            },
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert len(model.understand_calls) == 1


@pytest.mark.asyncio
async def test_feedback_ids_share_fail_closed_bucket_and_do_not_create_orphans(
    settings: Settings,
) -> None:
    limited = settings.model_copy(update={"feedback_rate_limit": 1})
    repository = InMemoryRepository()
    app = create_app(
        settings=limited,
        data=FakeData(),
        model=ScriptedModel(),
        repository=repository,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        first = await client.post("/v1/feedback", json={"requestId": "unknown-one", "rating": 1})
        second = await client.post("/v1/feedback", json={"requestId": "unknown-two", "rating": 1})

    assert first.status_code == 200
    assert first.json() == {"success": True}
    assert second.status_code == 429
    assert repository._student_feedback == {}


@pytest.mark.asyncio
async def test_rate_limiter_evicts_to_a_fixed_key_bound() -> None:
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, max_keys=3)

    for key in ("one", "two", "three", "four"):
        await limiter.check(key)

    assert list(limiter._events) == ["two", "three", "four"]
