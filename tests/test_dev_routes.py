"""The development-only routes behind the dev UI: gated, read-only, and true to the engine."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import create_app
from rockygpt_brain.boundary import (
    CAPABILITY_AFTER_LOOKUP_MESSAGE,
    CAPABILITY_MESSAGE,
    SAFETY_MESSAGE,
    SAFETY_TEXTS,
)
from rockygpt_brain.context import build_context
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import (
    AMBIGUOUS_TEXT,
    CLOCK_TEXT,
    HELP_TEXT,
    MODEL_INPUT_KEYS,
    RECALL_TEXT,
    SYSTEM_PROMPT,
    UNSUPPORTED_AFTER_LOOKUP_MESSAGE,
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
    assert texts["safety_other"] == SAFETY_MESSAGE
    assert {name: texts[f"safety_{name}"] for name in SAFETY_TEXTS} == SAFETY_TEXTS
    assert texts["campus_help"] == HELP_TEXT
    assert texts["capability"] == CAPABILITY_MESSAGE
    assert texts["capability_after_lookup"] == CAPABILITY_AFTER_LOOKUP_MESSAGE
    assert texts["unsupported"] == UNSUPPORTED_MESSAGE
    assert texts["unsupported_after_lookup"] == UNSUPPORTED_AFTER_LOOKUP_MESSAGE
    assert {"greeting", "thanks", "okay", "about", "not_found", "incomplete"} <= set(texts)
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


def test_the_search_says_what_a_lookup_would_do_using_the_engines_own_rule() -> None:
    shared = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[
            {"id": "a", "name": "Public Safety (Emergency)", "kind": "office",
             "aliases": ["Campus Police"], "links": []},
            {"id": "b", "name": "Public Safety (Non-Emergency)", "kind": "office",
             "aliases": ["Campus Police"], "links": []},
            {"id": "c", "name": "Registrar", "kind": "office", "aliases": [], "links": []},
        ],
        contacts=[], now=lambda: NOW)
    application = create_app(ChatEngine(NoGateway(), shared), service_token="",
                             environment="development")
    with TestClient(application) as http:
        def ask(text: str) -> dict[str, Any]:
            found = http.get("/v1/dev/offices/search", params={"q": text}, headers=ASKED)
            return dict(found.json())

        one_exact = ask("registrar")
        two_exact = ask("campus police")
        one_partial = ask("registr")
        none = ask("cafeteria")
    assert (one_exact["outcome"], one_exact["chosen"]) == ("answers", ["Registrar"])
    assert (two_exact["outcome"], two_exact["chosen"]) == ("asks", [])
    assert (one_partial["outcome"], one_partial["chosen"]) == ("answers", ["Registrar"])
    assert (none["outcome"], none["candidates"]) == ("not_found", [])


def test_every_fixed_text_says_who_picks_it_and_matches_the_template_the_engine_uses() -> None:
    with client() as http:
        body = http.get("/v1/dev/runtime", headers=ASKED).json()
    entries = {item["id"]: item for item in body["fixedTexts"]}
    assert all(item["pickedBy"] for item in entries.values())
    assert entries["ambiguous"]["text"] == AMBIGUOUS_TEXT.format(names="<up to five office names>")
    assert entries["clock"]["text"] == CLOCK_TEXT.format(
        when="<weekday, month day, year at time and zone>")
    assert entries["recall"]["text"] == RECALL_TEXT.format(
        speaker="<you or RockyGPT>", quote="> <the quoted message>")
    specific = ["the danger phrase list", "the model"]
    assert {k: v["pickedBy"] for k, v in entries.items()} == {
        "safety_self_harm": specific, "safety_medical": specific, "safety_danger": specific,
        "safety_fire": specific,
        "safety_other": ["the danger phrase list", "the model", "the code"],
        "campus_help": ["the code"],
        "capability": ["the model"], "capability_after_lookup": ["the model"],
        "unsupported": ["the model"], "unsupported_after_lookup": ["the model"],
        "clarification": ["the model", "the code"], "greeting": ["the model"],
        "thanks": ["the model"], "okay": ["the model"], "about": ["the model"],
        "ambiguous": ["the lookup result"], "ambiguous_more": ["the lookup result"],
        "not_found": ["the lookup result"], "data_unavailable": ["the lookup result"],
        "incomplete": ["a provider failure"], "clock": ["the model"], "recall": ["the model"],
        "recall_omitted": ["the model"]}
    assert body["nusdPerDollar"] == 1_000_000_000


def test_a_truncated_search_always_asks_even_with_one_exact_match() -> None:
    crowded = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": f"o{n}", "name": f"Student Office {n}", "kind": "office", "aliases": [],
                   "links": []} for n in range(12)],
        contacts=[], now=lambda: NOW)
    application = create_app(ChatEngine(NoGateway(), crowded), service_token="",
                             environment="development")
    with TestClient(application) as http:
        body = http.get("/v1/dev/offices/search", params={"q": "student office"},
                        headers=ASKED).json()
    assert body["truncated"] is True and body["outcome"] == "asks" and body["chosen"] == []
