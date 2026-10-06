"""The development-only routes behind the dev UI: gated, read-only, and true to the engine."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import create_app
from rockygpt_brain.boundary import CAPABILITY_MESSAGE, SAFETY_MESSAGE
from rockygpt_brain.context import build_context
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import (
    MODEL_INPUT_KEYS,
    SYSTEM_PROMPT,
    UNSUPPORTED_MESSAGE,
    AnswerPart,
    ChatEngine,
    model_input,
)
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.turn import intake

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
ASKED = {"x-rockygpt-diagnostics": "1"}


class NoGateway:
    async def complete(self, **_: Any) -> Any:  # pragma: no cover - the routes never call a model
        raise AssertionError("a dev route must not call the model")

    async def ready(self) -> bool:
        return True


def engine() -> ChatEngine:
    facts = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[
            {"id": "registrar", "name": "Registrar", "kind": "office", "aliases": ["Reg"],
             "links": []},
            {"id": "student-accounts", "name": "Student Accounts", "kind": "office",
             "aliases": [], "links": []},
            {"id": "someone", "name": "A Person", "kind": "person", "aliases": [], "links": []},
        ],
        contacts=[], now=lambda: NOW)
    return ChatEngine(NoGateway(), facts)


def client(environment: str = "development") -> TestClient:
    return TestClient(create_app(engine(), service_token="", environment=environment))


def test_the_routes_need_the_diagnostics_header() -> None:
    with client() as http:
        for path in ("/v1/dev/runtime", "/v1/dev/offices", "/v1/dev/offices/search?q=reg"):
            assert http.get(path).status_code == 404
            assert http.get(path, headers={"x-rockygpt-diagnostics": "yes"}).status_code == 404
            assert http.get(path, headers=ASKED).status_code == 200


def test_a_production_brain_has_no_dev_routes_even_when_asked() -> None:
    application = create_app(engine(), service_token="t", environment="production")  # noqa: S106
    with TestClient(application) as http:
        for path in ("/v1/dev/runtime", "/v1/dev/offices", "/v1/dev/offices/search?q=reg"):
            response = http.get(path, headers={**ASKED, "x-rockygpt-environment-token": "t"})
            assert response.status_code == 404


def test_the_routes_stay_out_of_the_published_schema() -> None:
    with client() as http:
        paths = http.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith("/v1/dev")]


def test_runtime_describes_the_prompt_tools_parts_and_fixed_texts_the_engine_really_uses() -> None:
    with client() as http:
        body = http.get("/v1/dev/runtime", headers=ASKED).json()
    assert body["prompt"] == SYSTEM_PROMPT and "published_offices" in body["prompt"]
    assert [tool["name"] for tool in body["tools"]] == ["office_facts", "finish"]
    assert body["tools"][0]["parameters"]["properties"]["requests"]["maxItems"] == 4
    kinds = AnswerPart.model_fields["kind"].annotation.__args__  # type: ignore[union-attr]
    assert [part["kind"] for part in body["parts"]] == list(kinds)
    texts = {item["id"]: item["text"] for item in body["fixedTexts"]}
    assert texts["safety"] == SAFETY_MESSAGE and texts["capability"] == CAPABILITY_MESSAGE
    assert texts["unsupported"] == UNSUPPORTED_MESSAGE
    assert {"greeting", "thanks", "about", "not_found", "incomplete"} <= set(texts)
    assert body["limits"]["maxTurnNusd"] == 25_000_000 and body["limits"]["maxModelCalls"] == 4
    assert body["model"] is None  # No real gateway here, so there is no price table.


def test_runtime_names_the_keys_the_model_is_really_given() -> None:
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": "hi"}]})
    state = model_input(intake(request, now=NOW), build_context(request), [])[1]["content"]
    assert tuple(json.loads(state)) == MODEL_INPUT_KEYS


def test_the_office_list_carries_ids_and_the_release_it_came_from() -> None:
    with client() as http:
        body = http.get("/v1/dev/offices", headers=ASKED).json()
    assert body["datasetVersion"] == "release-1" and body["identityHash"] == "identities-1"
    assert body["offices"] == [
        {"entityId": "registrar", "name": "Registrar", "aliases": ["Reg"]},
        {"entityId": "student-accounts", "name": "Student Accounts", "aliases": []},
    ]


def test_the_search_shows_who_matched_and_how() -> None:
    with client() as http:
        exact = http.get("/v1/dev/offices/search", params={"q": "Reg"}, headers=ASKED).json()
        partial = http.get("/v1/dev/offices/search", params={"q": "student"}, headers=ASKED).json()
        empty = http.get("/v1/dev/offices/search", params={"q": ""}, headers=ASKED)
    assert [(c["name"], c["match"]) for c in exact["candidates"]] == [("Registrar", "exact")]
    assert [(c["name"], c["match"]) for c in partial["candidates"]] == [
        ("Student Accounts", "partial")]
    assert empty.status_code == 422


@pytest.mark.parametrize("environment", ["development", "production"])
def test_the_chat_route_and_probes_are_unchanged_by_the_dev_routes(environment: str) -> None:
    application = create_app(engine(), service_token="t", environment=environment)  # noqa: S106
    with TestClient(application) as http:
        assert http.get("/health").status_code == 200
