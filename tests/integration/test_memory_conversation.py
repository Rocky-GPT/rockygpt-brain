from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from conftest import FakeData, ScriptedModel, make_shuttle_response, shuttle_plan
from rockygpt_brain.app import create_app
from rockygpt_brain.brain import Brain, TurnIdentity
from rockygpt_brain.capabilities import ShuttleCapability
from rockygpt_brain.config import Settings
from rockygpt_brain.contracts import ChatRequest
from rockygpt_brain.persistence import InMemoryRepository
from rockygpt_brain.security import pseudonymize


@pytest.mark.asyncio
async def test_server_ledger_answers_previous_utterance_and_topic_switches(
    settings: Settings,
) -> None:
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
async def test_followup_tomorrow_uses_request_timezone_relative_date(
    settings: Settings,
) -> None:
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
    service_date = data.queries[-1].service_date
    assert service_date is not None
    assert service_date.isoformat() == "2026-08-25"
    assert data.queries[-1].service_day == "weekday"


@pytest.mark.asyncio
async def test_failed_accepted_turn_does_not_mutate_memory(settings: Settings) -> None:
    repository = InMemoryRepository()
    invalid = make_shuttle_response(outcome="empty", records=False, evidence=False)
    app = create_app(
        settings=settings,
        data=FakeData([invalid]),
        model=ScriptedModel(),
        repository=repository,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post(
            "/v1/chat",
            json={
                "conversationId": "failed-conversation",
                "message": "What is the next shuttle to GSP?",
            },
        )

    session_id = pseudonymize(
        "failed-conversation",
        settings.secret_value(settings.chat_log_hash_key),
        "conversation",
    )
    memory = await repository.load_memory(session_id)
    logs = await repository.list_logs(search=None, routes=set(), origins=set(), limit=10)
    assert response.status_code == 503
    assert memory.recent_turns == ()
    assert memory.claims == ()
    assert logs.logs == []
    assert logs.metrics.error_count == 1


@pytest.mark.asyncio
async def test_pinned_semantic_time_cannot_control_persistence_retention() -> None:
    trusted_now = datetime(2026, 8, 24, 16, 30, tzinfo=UTC)
    repository = InMemoryRepository()
    brain = Brain(
        model=ScriptedModel(),
        shuttle=ShuttleCapability(FakeData()),
        repository=repository,
        clock=lambda: trusted_now,
    )

    await brain.answer(
        ChatRequest(
            message="Is SGA overrated?",
            now=datetime(2099, 1, 1, tzinfo=UTC),
        ),
        TurnIdentity(
            request_id="trusted-clock-request",
            session_id="trusted-clock-session",
            visitor_id=None,
            safety_identifier="trusted-clock-safety-id",
            question_origin="dev",
        ),
    )

    logs = await repository.list_logs(search=None, routes=set(), origins=set(), limit=10)
    assert logs.logs[0].created_at == trusted_now


@pytest.mark.asyncio
async def test_exact_source_recall_cannot_be_misrouted_to_shuttle(
    settings: Settings,
) -> None:
    data = FakeData()
    model = ScriptedModel()
    app = create_app(
        settings=settings,
        data=data,
        model=model,
        repository=InMemoryRepository(),
    )
    common = {"conversationId": "route-guard", "visitorId": "route-guard"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        first = await client.post(
            "/v1/chat",
            json={**common, "message": "What is the first shuttle to GSP?"},
        )
        model.plan_queue.append(shuttle_plan(destination="GSP"))
        recalled = await client.post(
            "/v1/chat",
            json={**common, "message": "What source supported that earlier answer?"},
        )

    assert first.status_code == 200
    assert recalled.status_code == 200
    assert recalled.json()["answer"] == "The source I used then was Official Shuttle Schedule."
    assert recalled.json()["citations"][0]["sourceId"] == "transportation"
    assert len(data.queries) == 1
