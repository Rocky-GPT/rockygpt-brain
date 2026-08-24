from __future__ import annotations

import asyncio

import httpx
import pytest

from conftest import FakeData, ScriptedModel
from rockygpt_brain import brain as brain_module
from rockygpt_brain.app import create_app
from rockygpt_brain.config import Settings
from rockygpt_brain.errors import ModelOutputError
from rockygpt_brain.model import CommunicateInput, UnderstandInput
from rockygpt_brain.persistence import InMemoryRepository
from rockygpt_brain.planning import AnswerDraft, RouteMode, RoutePlan


class SlowUnderstandRepairModel(ScriptedModel):
    async def understand(
        self,
        request: UnderstandInput,
        *,
        repair_error: str | None = None,
    ) -> RoutePlan:
        self.understand_calls.append(request)
        await asyncio.sleep(0.025)
        if repair_error is None:
            raise ModelOutputError("repair me")
        return RoutePlan(mode=RouteMode.GENERAL)


class SlowCommunicateCorrectionModel(ScriptedModel):
    async def communicate(
        self,
        request: CommunicateInput,
        *,
        correction_error: str | None = None,
    ) -> AnswerDraft:
        self.communicate_calls.append(request)
        await asyncio.sleep(0.025)
        if correction_error is None:
            raise ModelOutputError("correct me")
        return AnswerDraft(
            answer="This would succeed if the correction received a fresh budget.",
            route="standard",
            claims=[],
            citationEvidenceIds=[],
            uiActions=[],
            suggestedQuestions=[],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("budget_name", "model"),
    [
        ("UNDERSTAND_BUDGET_SECONDS", SlowUnderstandRepairModel()),
        ("COMMUNICATE_BUDGET_SECONDS", SlowCommunicateCorrectionModel()),
    ],
)
async def test_repair_and_correction_share_one_stage_budget(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    budget_name: str,
    model: ScriptedModel,
) -> None:
    monkeypatch.setattr(brain_module, budget_name, 0.04)
    app = create_app(
        settings=settings,
        data=FakeData(),
        model=model,
        repository=InMemoryRepository(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://brain"
    ) as client:
        response = await client.post("/v1/chat", json={"message": "Is SGA overrated?"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    calls = (
        model.understand_calls
        if budget_name == "UNDERSTAND_BUDGET_SECONDS"
        else model.communicate_calls
    )
    assert len(calls) == 2
