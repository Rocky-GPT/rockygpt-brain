from __future__ import annotations

from datetime import date

import httpx
import pytest

from conftest import FakeData, ScriptedModel, make_shuttle_response
from rockygpt_brain.app import create_app
from rockygpt_brain.persistence import InMemoryRepository


@pytest.mark.asyncio
async def test_first_shuttle_keeps_destination_distinct_from_route(settings: object) -> None:
    data = FakeData()
    model = ScriptedModel()
    repo = InMemoryRepository()
    app = create_app(settings=settings, data=data, model=model, repository=repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post(
            "/v1/chat",
            json={
                "message": "What is the first shuttle to GSP?",
                "now": "2026-08-24T13:00:00Z",
                "timezone": "America/New_York",
                "conversationId": "conversation-1",
                "visitorId": "visitor-1",
            },
        )

    assert response.status_code == 200, response.text
    assert response.headers["x-request-id"] == response.json()["requestId"]
    assert response.json()["citations"][0]["title"] == "Official Shuttle Schedule"
    assert response.json()["uiActions"] == [{"type": "VIEW_BUS"}]
    query = data.queries[0]
    assert query.destination == "GSP"
    assert query.route is None
    assert query.selection.value == "first"
    assert query.time_scope.value == "full_day"
    assert query.service_date == date(2026, 8, 24)
    assert query.service_day == "weekday"


@pytest.mark.asyncio
async def test_next_shuttle_keeps_route_out_of_destination(settings: object) -> None:
    data = FakeData()
    app = create_app(
        settings=settings,
        data=data,
        model=ScriptedModel(),
        repository=InMemoryRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post(
            "/v1/chat",
            json={
                "message": "What is the next shuttle on Route A?",
                "now": "2026-08-24T13:00:00Z",
                "timezone": "America/New_York",
            },
        )
    assert response.status_code == 200
    assert data.queries[0].route == "Route A"
    assert data.queries[0].destination is None
    assert data.queries[0].selection.value == "next"
    assert data.queries[0].time_scope.value == "remaining"


@pytest.mark.asyncio
async def test_authoritative_empty_is_200_and_still_cited(settings: object) -> None:
    empty = make_shuttle_response(outcome="empty", records=False, evidence=True)
    app = create_app(
        settings=settings,
        data=FakeData([empty]),
        model=ScriptedModel(),
        repository=InMemoryRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post(
            "/v1/chat",
            json={"message": "What is the next shuttle to GSP?", "now": "2026-08-24T23:00:00Z"},
        )
    assert response.status_code == 200
    assert "No matching shuttle" in response.json()["answer"]
    assert response.json()["citations"][0]["sourceId"] == "transportation"


@pytest.mark.asyncio
async def test_empty_without_source_is_dependency_failure(settings: object) -> None:
    invalid_empty = make_shuttle_response(outcome="empty", records=False, evidence=False)
    app = create_app(
        settings=settings,
        data=FakeData([invalid_empty]),
        model=ScriptedModel(),
        repository=InMemoryRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post(
            "/v1/chat", json={"message": "What is the next shuttle to GSP?"}
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATASET_UNAVAILABLE"
