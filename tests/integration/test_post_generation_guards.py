from __future__ import annotations

import httpx
import pytest

from conftest import FakeData, ScriptedModel
from rockygpt_brain.app import create_app
from rockygpt_brain.config import Settings
from rockygpt_brain.errors import ModelOutputError
from rockygpt_brain.persistence import InMemoryRepository
from rockygpt_brain.planning import AnswerDraft, RouteMode, RoutePlan


def _general_draft(answer: str) -> AnswerDraft:
    return AnswerDraft(
        answer=answer,
        route="standard",
        claims=[],
        citationEvidenceIds=[],
        uiActions=[],
        suggestedQuestions=[],
    )


@pytest.mark.asyncio
async def test_model_secret_is_rejected_then_replaced_by_verified_fallback(
    settings: Settings,
) -> None:
    model = ScriptedModel()
    model.plan_queue.append(RoutePlan(mode=RouteMode.GENERAL))
    leaked = _general_draft("Here is a credential: sk-abcdefghijklmnop1234")
    model.draft_queue.extend([leaked, leaked])
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=model,
        repository=InMemoryRepository(),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post("/v1/chat", json={"message": "Tell me a harmless joke."})

    assert response.status_code == 200
    assert response.json()["route"] == "ungrounded"
    assert "sk-" not in response.json()["answer"]
    assert len(model.communicate_calls) == 2


@pytest.mark.asyncio
async def test_invalid_shuttle_model_output_falls_back_to_exact_code_projection(
    settings: Settings,
) -> None:
    model = ScriptedModel()
    model.draft_queue.extend(
        [ModelOutputError("invalid draft"), ModelOutputError("invalid repaired draft")]
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
            "/v1/chat",
            json={
                "message": "What is the first shuttle to GSP?",
                "now": "2026-08-24T13:00:00Z",
            },
        )

    assert response.status_code == 200
    assert response.json()["answer"] == (
        "Route A leaves Campus at 9:00 AM and reaches GSP at 9:20 AM."
    )
    assert response.json()["citations"][0]["sourceId"] == "transportation"
    assert response.json()["uiActions"] == [{"type": "VIEW_BUS"}]
