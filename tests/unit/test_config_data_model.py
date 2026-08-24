from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from rockygpt_brain.config import Settings
from rockygpt_brain.data_client import HttpDataV2Client, ShuttleQuery
from rockygpt_brain.memory import MemorySnapshot
from rockygpt_brain.model import CommunicateInput, OpenAIResponsesModel, UnderstandInput
from rockygpt_brain.planning import (
    AnswerDraft,
    RouteMode,
    RoutePlan,
    ShuttleSelection,
    ShuttleTimeScope,
)
from rockygpt_brain.time_context import TimeContext


def test_blank_optional_secrets_are_unset() -> None:
    settings = Settings(
        app_env="test",
        openai_api_key="",
        database_url=" ",
        chat_log_hash_key="",
        admin_api_token="",
        abuse_hash_key="",
        staging_service_token="",
    )
    assert settings.openai_api_key is None
    assert settings.database_url is None
    assert settings.chat_log_hash_key is None
    assert settings.admin_api_token is None
    assert settings.abuse_hash_key is None
    assert settings.staging_service_token is None


@pytest.mark.asyncio
async def test_authenticated_data_readiness_detects_bad_environment_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/readiness":
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://data") as http_client:
        client = HttpDataV2Client(
            "http://data", environment_token="wrong-token", client=http_client
        )
        assert not await client.readiness()
    assert len(seen) == 2
    assert seen[1].headers["x-rockygpt-environment-token"] == "wrong-token"


@pytest.mark.asyncio
async def test_data_v2_client_sends_exact_optional_query_shape() -> None:
    captured: dict[str, Any] = {}
    response_body = {
        "outcome": "empty",
        "records": [],
        "completeness": {
            "state": "complete",
            "returned": 0,
            "matched": 0,
            "limit": 50,
            "truncated": False,
            "reason": "no_remaining",
        },
        "appliedFilters": {
            "destination": "GSP",
            "serviceDate": "2026-08-24",
            "serviceDay": "weekday",
            "asOf": "2026-08-24T17:00:00Z",
            "selection": "next",
            "timeScope": "remaining",
            "serviceDatesConsidered": ["2026-08-24"],
        },
        "ordering": [{"field": "matchedDestination.time", "direction": "asc"}],
        "dataset": {
            "id": "transportation",
            "version": "v1",
            "activatedAt": "2026-08-24T12:00:00Z",
        },
        "evidence": [
            {
                "evidenceId": "schedule",
                "sourceId": "transportation",
                "title": "Official schedule",
                "url": "https://example.edu/shuttle",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=response_body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://data"
    ) as http_client:
        client = HttpDataV2Client("http://data", client=http_client)
        result = await client.query_shuttle(
            ShuttleQuery(
                destination="GSP",
                service_date="2026-08-24",
                service_day="weekday",
                as_of="2026-08-24T17:00:00Z",
                selection=ShuttleSelection.NEXT,
                time_scope=ShuttleTimeScope.REMAINING,
                limit=None,
            )
        )
    assert captured["path"] == "/v2/capabilities/shuttle/query"
    assert "limit" not in captured["body"]
    assert captured["body"]["destination"] == "GSP"
    assert result.completeness.reason == "no_remaining"


class FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        output = (
            RoutePlan(mode=RouteMode.GENERAL)
            if kwargs["text_format"] is RoutePlan
            else AnswerDraft(
                answer="Four.",
                route="standard",
                claims=[],
                citationEvidenceIds=[],
                uiActions=[],
                suggestedQuestions=[],
            )
        )
        return SimpleNamespace(output_parsed=output)


@pytest.mark.asyncio
async def test_openai_responses_calls_are_structured_stateless_and_store_false() -> None:
    responses = FakeResponses()
    fake_client = SimpleNamespace(responses=responses)
    model = OpenAIResponsesModel(api_key=None, model="test-model", client=fake_client)
    time = TimeContext.create(
        pinned_now=datetime(2026, 8, 24, 13, tzinfo=UTC), requested_timezone="America/New_York"
    )
    plan = await model.understand(
        UnderstandInput(
            message="What is 2 + 2?",
            client_history=(),
            memory=MemorySnapshot(),
            time=time,
            safety_identifier="safe-id",
        )
    )
    await model.communicate(
        CommunicateInput(
            message="What is 2 + 2?",
            plan=plan,
            typed_results=({"kind": "general"},),
            evidence=(),
            memory=MemorySnapshot(),
            style_mode=None,
            response_mode=None,
            safety_identifier="safe-id",
        )
    )
    assert len(responses.calls) == 2
    assert all(call["store"] is False for call in responses.calls)
    assert responses.calls[0]["text_format"] is RoutePlan
    assert responses.calls[1]["text_format"] is AnswerDraft
