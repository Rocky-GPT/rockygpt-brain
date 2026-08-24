from __future__ import annotations

from datetime import date

import httpx
import pytest

from conftest import FakeData, ScriptedModel, make_shuttle_response, shuttle_plan
from rockygpt_brain.app import create_app
from rockygpt_brain.config import Settings
from rockygpt_brain.persistence import InMemoryRepository
from rockygpt_brain.planning import (
    AnswerDraft,
    ClaimKind,
    DraftClaim,
    ShuttleSelection,
    ShuttleTimeScope,
)


@pytest.mark.asyncio
async def test_first_shuttle_keeps_destination_distinct_from_route(settings: Settings) -> None:
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
async def test_next_shuttle_keeps_route_out_of_destination(settings: Settings) -> None:
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
async def test_authoritative_empty_is_200_and_still_cited(settings: Settings) -> None:
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
async def test_partial_shuttle_result_is_disclosed(settings: Settings) -> None:
    partial = make_shuttle_response()
    partial.completeness.state = "partial"
    partial.completeness.truncated = True
    partial.completeness.reason = "limit"
    app = create_app(
        settings=settings,
        data=FakeData([partial]),
        model=ScriptedModel(),
        repository=InMemoryRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post(
            "/v1/chat", json={"message": "What is the next shuttle to GSP?"}
        )

    assert response.status_code == 200
    assert response.json()["answer"].endswith(
        "This is a partial result; additional matching trips may exist."
    )


@pytest.mark.asyncio
async def test_entity_no_match_is_distinct_from_no_remaining(settings: Settings) -> None:
    no_match = make_shuttle_response(outcome="no_match", records=False)
    no_match.completeness.reason = "entity_no_match"
    no_remaining = make_shuttle_response(outcome="empty", records=False)
    no_remaining.completeness.reason = "no_remaining"
    app = create_app(
        settings=settings,
        data=FakeData([no_match, no_remaining]),
        model=ScriptedModel(),
        repository=InMemoryRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        missed = await client.post("/v1/chat", json={"message": "What is the next shuttle to GSP?"})
        elapsed = await client.post(
            "/v1/chat", json={"message": "What is the next shuttle to GSP?"}
        )

    assert missed.json()["answer"] == (
        "I couldn’t find a shuttle matching the requested route or stop."
    )
    assert elapsed.json()["answer"] == (
        "No matching shuttle remains in the requested service period."
    )


@pytest.mark.asyncio
async def test_empty_without_source_is_dependency_failure(settings: Settings) -> None:
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


@pytest.mark.asyncio
async def test_origin_aware_answer_uses_matched_pickup_not_trip_departure(
    settings: Settings,
) -> None:
    response_data = make_shuttle_response(
        matched_origin=("Ramsey Station", "9:12 AM"),
        matched_destination=("Campus", "9:40 AM"),
    )
    model = ScriptedModel()
    model.plan_queue.append(
        shuttle_plan(
            origin="Ramsey Station",
            destination="Campus",
            selection=ShuttleSelection.NEXT,
            scope=ShuttleTimeScope.REMAINING,
        )
    )
    app = create_app(
        settings=settings,
        data=FakeData([response_data]),
        model=model,
        repository=InMemoryRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post(
            "/v1/chat", json={"message": "What is next from Ramsey Station to campus?"}
        )
    assert response.status_code == 200
    assert response.json()["answer"] == (
        "Route A leaves Ramsey Station at 9:12 AM and reaches Campus at 9:40 AM."
    )


@pytest.mark.asyncio
async def test_two_bad_ai_communications_fall_back_to_exact_code_projection(
    settings: Settings,
) -> None:
    model = ScriptedModel()
    bad = AnswerDraft(
        answer="The shuttle leaves at 7:77 PM.",
        route="standard",
        claims=[
            DraftClaim(
                text="The shuttle leaves at 7:77 PM.",
                kind=ClaimKind.CAMPUS,
                evidenceIds=["shuttle-source-1"],
            )
        ],
        citationEvidenceIds=["shuttle-source-1"],
        uiActions=[],
        suggestedQuestions=[],
    )
    model.draft_queue.extend([bad, bad])
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
            "/v1/chat", json={"message": "What is the next shuttle to GSP?"}
        )
    assert response.status_code == 200
    assert response.json()["answer"] == (
        "Route A leaves Campus at 9:00 AM and reaches GSP at 9:20 AM."
    )
    assert response.json()["citations"][0]["sourceId"] == "transportation"
    assert len(model.communicate_calls) == 2


@pytest.mark.asyncio
async def test_shuttle_replaces_model_actions_with_exact_view_bus_action(
    settings: Settings,
) -> None:
    model = ScriptedModel()
    canonical = "Route A leaves Campus at 9:00 AM and reaches GSP at 9:20 AM."
    model.draft_queue.append(
        AnswerDraft(
            answer=canonical,
            route="standard",
            claims=[
                DraftClaim(
                    text=canonical,
                    kind=ClaimKind.CAMPUS,
                    evidenceIds=["shuttle-source-1"],
                )
            ],
            citationEvidenceIds=["shuttle-source-1"],
            uiActions=[{"type": "VIEW_DIRECTORY"}],
            suggestedQuestions=[],
        )
    )
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
            "/v1/chat", json={"message": "What is the next shuttle to GSP?"}
        )

    assert response.status_code == 200
    assert response.json()["uiActions"] == [{"type": "VIEW_BUS"}]
