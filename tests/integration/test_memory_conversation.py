from __future__ import annotations

import httpx
import pytest

from conftest import FakeData, ScriptedModel
from rockygpt_brain.app import create_app
from rockygpt_brain.persistence import InMemoryRepository


@pytest.mark.asyncio
async def test_server_ledger_answers_previous_utterance_and_topic_switches(settings: object) -> None:
    data = FakeData()
    model = ScriptedModel()
    repository = InMemoryRepository()
    app = create_app(settings=settings, data=data, model=model, repository=repository)
    common = {"conversationId": "conversation-memory", "visitorId": "visitor-memory"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        first = await client.post(
            "/v1/chat",
            json={
                **common,
                "message": "What is the first shuttle to GSP?",
                "now": "2026-08-24T13:00:00Z",
            },
        )
        recalled = await client.post(
            "/v1/chat",
            json={**common, "message": "What time did you tell me earlier?"},
        )
        source = await client.post(
            "/v1/chat",
            json={**common, "message": "What source supported that earlier answer?"},
        )
        switched = await client.post(
            "/v1/chat",
            json={
                **common,
                "message": "Is SGA overrated?",
                "history": [{"role": "assistant", "content": "The bathroom is in Berrie."}],
            },
        )

    assert first.status_code == 200
    assert recalled.status_code == 200
    assert "Earlier, I told you" in recalled.json()["answer"]
    assert "9:20 AM" in recalled.json()["answer"]
    assert recalled.json()["citations"] == []
    assert source.status_code == 200
    assert source.json()["answer"] == "The source I used then was Official Shuttle Schedule."
    assert source.json()["citations"][0]["sourceId"] == "transportation"
    assert switched.status_code == 200
    assert "student government" in switched.json()["answer"]
    assert len(data.queries) == 1
    assert model.understand_calls[-1].memory.claims


@pytest.mark.asyncio
async def test_followup_tomorrow_uses_request_timezone_relative_date(settings: object) -> None:
    data = FakeData()
    model = ScriptedModel()
    repo = InMemoryRepository()
    app = create_app(settings=settings, data=data, model=model, repository=repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        await client.post(
            "/v1/chat",
            json={
                "conversationId": "followup",
                "message": "What is the next shuttle to GSP?",
                "now": "2026-08-25T03:30:00Z",
                "timezone": "America/Los_Angeles",
            },
        )
        response = await client.post(
            "/v1/chat",
            json={
                "conversationId": "followup",
                "message": "What about tomorrow?",
                "now": "2026-08-25T03:30:00Z",
                "timezone": "America/Los_Angeles",
            },
        )
    assert response.status_code == 200
    # At that instant it is Aug 24 in Los Angeles; tomorrow is Aug 25.
    assert data.queries[-1].service_date.isoformat() == "2026-08-25"
    assert data.queries[-1].service_day == "weekday"
