"""Actual PostgreSQL retrieval and HTTP/accounting path, with only the provider injected."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx import Request, Response
from openai import APITimeoutError, AuthenticationError
from phase2_snapshot import load_snapshot, local_database

from rockygpt_brain.accounting import PostgresLedger
from rockygpt_brain.api.app import app
from rockygpt_brain.config import MONTHLY_CAP_NUSD, RELEASE, Deployment
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.data import CampusData, SearchQuery
from rockygpt_brain.engine import run_turn
from rockygpt_brain.evidence import map_references, reference_aliases
from rockygpt_brain.exact import ContactQuery
from rockygpt_brain.provider import ModelResponse, OutputItem, PaidGateway, Usage, input_bound
from test_accounting import database as database
from test_accounting import ledger as ledger
from test_engine import tools
from test_evidence import expand_records


@pytest.fixture(scope="module")
def frozen(database: str) -> dict[str, Any]:
    return load_snapshot()


@pytest.fixture
def data(frozen: dict[str, Any]) -> Iterator[CampusData]:
    repository = CampusData(local_database(), datetime.fromisoformat(frozen["captured_at"]))
    yield repository
    repository.close()


def test_actual_contact_sql_pins_identity_aliases_and_fields(data: CampusData) -> None:
    query = ContactQuery(entity="Office of the Registrar", fields=["phone", "email", "fax"])
    output = data.lookup_contact(query)
    assert len(output["records"]) == 1
    record = output["records"][0]
    assert record["entity_id"] == "campus-directory:office:registrar"
    assert record["fields"]["phone"] == "201-684-7695"
    assert record["coverage"]["fields"]["fax"] == "not_published"
    assert not output["truncated"]
    assert (
        data.lookup_contact(ContactQuery(entity="' OR true --", fields=["phone"]))["records"] == []
    )
    assert data.lookup_contact(ContactQuery(entity="Registrar", fields=["phone"]))["records"]


def test_actual_filters_intersect_dates_and_dietary_labels(data: CampusData) -> None:
    query = SearchQuery.model_validate(
        {
            "collection": "menu",
            "date_from": "2026-09-16",
            "limit": 50,
            "filters": {"meal": "Lunch", "vegan": True},
        }
    )
    output = data.search(query)
    assert output["records"]
    assert all(
        r["fields"]["meal"] == "Lunch"
        and r["fields"]["vegan"] is True
        and r["valid_from"] == "2026-09-16"
        for r in output["records"]
    )
    # Old publications coerced missing flags to false; they cannot support negative filters.
    query.filters.vegan = False  # type: ignore[union-attr]
    assert data.search(query)["records"] == []


def test_captured_short_venue_request_renders_frozen_menu_after_one_model_call(
    data: CampusData, frozen: dict[str, Any]
) -> None:
    question = "What vegetarian options are on Birch's dinner menu today?"
    client = Mock()
    client.create.return_value = tools(
        SimpleNamespace(
            type="function_call",
            name="search_campus",
            call_id="menu",
            arguments=json.dumps(
                {
                    "collection": "menu",
                    "query": "Birch",
                    "date_from": "2026-09-16",
                    "date_to": "2026-09-16",
                    "limit": 100,
                    "filters": {"meal": "Dinner", "vegetarian": True},
                    "request_text": question,
                }
            ),
        )
    )
    result = run_turn(
        [ChatMessage(role="user", content=question)],
        client=client,
        data=data,
        model="test",
        now=datetime.fromisoformat(frozen["captured_at"]),
    )
    assert result["metrics"]["responseMode"] == "exact_records"
    assert client.create.call_count == 1
    assert result["status"] == "answered"
    assert "Birch Tree Inn" in result["answer"]
    assert "2026-09-16" in result["answer"]
    assert len(result["citations"]) == result["trace"][0]["total_matches"] == 39


def test_calendar_filters_never_mix_terms_or_sessions(data: CampusData) -> None:
    output = data.search(
        SearchQuery.model_validate(
            {
                "collection": "calendar",
                "query": "withdraw",
                "filters": {"term": "Fall 2026", "session": "Session I"},
                "limit": 50,
            }
        )
    )
    assert output["records"]
    assert all(
        r["fields"]["term"] == "Fall 2026" and r["fields"]["session"] == "Session I"
        for r in output["records"]
    )


@pytest.mark.parametrize("oversized", [False, True])
def test_short_dinner_chat_with_fifty_menu_records_and_hours(
    frozen: dict[str, Any],
    data: CampusData,
    ledger: PostgresLedger,
    oversized: bool,
) -> None:
    """Reproduce the actual failing retrieval through HTTP, SQL and paid admission.

    Only the external provider is simulated. Force the troublesome tool choices
    rather than relying on a live model to happen to ask for fifty records again.
    """
    now = datetime.fromisoformat(frozen["captured_at"])
    queries = [
        {
            "collection": "menu",
            "date_from": "2026-09-16",
            "limit": 50,
            "filters": {"meal": "Dinner"},
        },
        {"collection": "dining_hours", "date_from": "2026-09-16", "limit": 50},
    ]
    expected = [data.search(SearchQuery.model_validate(query)) for query in queries]
    assert [len(result["records"]) for result in expected] == [50, 4]
    assert expected[0]["total_matches"] == 51 and expected[0]["truncated"]
    messages = [
        {"role": "user", "content": "hey"},
        {"role": "assistant", "content": "Hey! How can I help with Ramapo today?"},
        {"role": "user", "content": "what is for dinner today"},
    ]
    menu, hours = [result["records"] for result in expected]
    original_ids = {
        alias: record_id
        for record_id, alias in reference_aliases([r["id"] for r in menu + hours]).items()
    }
    parts = [
        {
            "kind": "campus_fact",
            "text": "Dinner menu: " + ", ".join(r["title"] for r in menu),
            "evidence_ids": [r["id"] for r in menu],
        },
        {
            "kind": "campus_fact",
            "text": "Birch Tree Inn lists dinner from 5 to 8 PM.",
            "evidence_ids": [r["id"] for r in hours if r["title"] == "Birch Tree Inn"],
        },
        {
            "kind": "limitation",
            "text": "These are 50 of 51 matching items, not the full menu.",
            "evidence_ids": [],
        },
    ]
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> ModelResponse:
        calls.append(kwargs)
        output: list[OutputItem] = []
        text = ""
        if len(calls) == 1:
            output = [
                OutputItem(
                    {
                        "type": "function_call",
                        "name": "search_campus",
                        "call_id": str(index),
                        "arguments": json.dumps(query),
                    }
                )
                for index, query in enumerate(queries)
            ]
        elif len(calls) == 2:
            assert kwargs["input"][:3] == messages  # Never drop accepted conversation.
            payload = {
                k: v
                for k, v in kwargs.items()
                if k not in {"timeout", "service_tier", "truncation"}
            }
            legacy_history = []
            originals = iter(expected)
            for item in kwargs["input"]:
                if item.get("type") == "function_call_output":
                    item = {**item, "output": json.dumps(next(originals), ensure_ascii=False)}
                legacy_history.append(item)
            # The original representation fails the unchanged gateway ceiling;
            # the same complete evidence now fits, with no inflated token cap.
            assert input_bound({**payload, "input": legacy_history}) > RELEASE.max_input_tokens
            assert input_bound(payload) <= RELEASE.max_input_tokens
            outputs = [
                map_references(json.loads(item["output"]), original_ids)
                for item in kwargs["input"]
                if item.get("type") == "function_call_output"
            ]
            for result, original in zip(outputs, expected, strict=True):
                assert expand_records(result.pop("evidence_groups")) == original["records"]
                assert result == {k: v for k, v in original.items() if k != "records"}
            text = json.dumps({"status": "partial", "parts": parts})
        else:
            assert len(calls) == 3
            review_input = map_references(json.loads(kwargs["input"]), original_ids)
            assert review_input["conversation"] == messages
            assert expand_records(review_input["evidence"]) == menu + hours
            text = json.dumps(
                {
                    "parts": [
                        {
                            "part_index": i,
                            "verdict": "supported",
                            "reason": "",
                            "unverified_premises": [],
                            "uses_event_for_entity": False,
                            "infers_food_safety": False,
                            "plan_deadlines": [],
                        }
                        for i in range(len(parts))
                    ]
                }
            )
        return ModelResponse(
            "fixture-" + str(uuid4()),
            RELEASE.model,
            "completed",
            text,
            output,
            Usage(250, 0, 40, 0),
        )

    provider = Mock()
    provider.create.side_effect = create
    if oversized:
        original_search = data.search

        def large_search(query: SearchQuery) -> dict[str, Any]:
            result = original_search(query)
            # Simulate an oversized detailed source after the same actual SQL read.
            result["records"][0]["fields"]["detail"] = "Long retrieved evidence. " * 5000
            return result

        data.search = large_search  # type: ignore[method-assign]
    deployment = Deployment(
        environment="development", api_key="fixture", project="fixture", ledger_url=local_database()
    )

    @contextmanager
    def gateway(config: Deployment, request_id: str) -> Iterator[PaidGateway]:
        yield PaidGateway(provider, ledger, request_id, clock=lambda: now)

    with (
        patch("rockygpt_brain.api.app.load_deployment", return_value=deployment),
        patch("rockygpt_brain.api.app.open_gateway", gateway),
        patch("rockygpt_brain.api.app.CampusData", return_value=data),
        patch("rockygpt_brain.api.app.datetime") as clock,
        patch.dict("os.environ", {"STAGING_SERVICE_TOKEN": ""}),
    ):
        clock.now.return_value = now
        response = TestClient(app).post("/v1/chat", json={"messages": messages})
    payload = response.json()
    assert payload["requestId"]
    operations = ledger.operations()
    assert all(op["state"] == "settled" for op in operations)
    assert all(op["request_id"] == payload["requestId"] for op in operations)
    if oversized:
        assert response.status_code == 422
        assert payload["reason"] == "retrieval_context_limit"
        assert "conversation" not in payload["error"]["message"].lower()
        assert payload["error"]["retryable"] is False
        assert len(calls) == len(operations) == 1  # No reservation or SDK call for overflow.
    else:
        assert response.status_code == 200
        assert payload["status"] == "partial"
        assert all(r["title"] in payload["answer"] for r in menu)
        assert len(payload["citations"]) == 51
        assert payload["metrics"]["usageComplete"]
        assert payload["metrics"]["reviewCalls"] == 1
        assert len(calls) == len(operations) == 3


def provider_response(entity: str = "Registrar", fields: list[str] | None = None) -> ModelResponse:
    return ModelResponse(
        "fixture-" + str(uuid4()),
        RELEASE.model,
        "completed",
        "",
        [
            OutputItem(
                {
                    "type": "function_call",
                    "name": "lookup_contact",
                    "call_id": "lookup",
                    "arguments": json.dumps({"entity": entity, "fields": fields or ["phone"]}),
                }
            )
        ],
        Usage(250, 0, 40, 0),
    )


@pytest.mark.parametrize(
    "scenario",
    [
        "supported",
        "missing",
        "uncovered",
        "ambiguous",
        "conflict",
        "database",
        "provider",
        "timeout",
        "budget",
    ],
)
def test_http_path_preserves_accounting_for_controlled_failures(
    scenario: str,
    frozen: dict[str, Any],
    data: CampusData,
    ledger: PostgresLedger,
) -> None:
    now = datetime.fromisoformat(frozen["captured_at"])
    provider = Mock()
    provider.create.return_value = provider_response()
    question = "What is Registrar's phone?"
    if scenario == "missing":
        provider.create.return_value = provider_response("Unknown Office")
        question = "What is Unknown Office's phone?"
    if scenario == "uncovered":
        provider.create.return_value = provider_response(fields=["fax"])
        question = "What is Registrar's fax?"
    injected_id = None
    if scenario in {"ambiguous", "conflict"}:
        with psycopg.connect(local_database()) as conn:
            row = conn.execute(
                "INSERT INTO rockygpt_v2.campus_contacts "
                "(dataset_version_id,source_id,source_record_key,name,department,phone,"
                "email,office,collected_at,content_hash,aliases) "
                "SELECT dataset_version_id,source_id,%s,%s,department,%s,email,office,"
                "collected_at,content_hash,aliases FROM rockygpt_v2.campus_contacts "
                "WHERE source_record_key='office:registrar' LIMIT 1 RETURNING id",
                (
                    "office:registrar" if scenario == "conflict" else "office:other-registrar",
                    "Registrar" if scenario == "conflict" else "Other Registrar",
                    "201-555-0199",
                ),
            ).fetchone()
            assert row
            injected_id = row[0]
        provider.create.return_value = provider_response("Office of the Registrar")
        question = "What is Office of the Registrar's phone?"
    if scenario == "provider":
        provider.create.side_effect = AuthenticationError(
            "invalid credential",
            response=Response(401, request=Request("POST", "https://api.openai.com")),
            body=None,
        )
    if scenario == "database":
        data.lookup_contact = Mock(side_effect=RuntimeError("secret connection details"))  # type: ignore[method-assign]
    if scenario == "timeout":
        provider.create.side_effect = APITimeoutError(
            request=Request("POST", "https://api.openai.com")
        )
    if scenario == "budget":
        ledger.reserve(str(uuid4()), "preexisting", "draft", MONTHLY_CAP_NUSD, {}, now)
    deployment = Deployment(
        environment="development", api_key="fixture", project="fixture", ledger_url=local_database()
    )

    @contextmanager
    def gateway(config: Deployment, request_id: str) -> Iterator[PaidGateway]:
        yield PaidGateway(provider, ledger, request_id, clock=lambda: now)

    with (
        patch("rockygpt_brain.api.app.load_deployment", return_value=deployment),
        patch("rockygpt_brain.api.app.open_gateway", gateway),
        patch("rockygpt_brain.api.app.CampusData", return_value=data),
        patch.dict("os.environ", {"STAGING_SERVICE_TOKEN": ""}),
    ):
        response = TestClient(app).post(
            "/v1/chat", json={"messages": [{"role": "user", "content": question}]}
        )
    if injected_id:
        with psycopg.connect(local_database()) as conn:
            conn.execute("DELETE FROM rockygpt_v2.campus_contacts WHERE id=%s", (injected_id,))
    payload = response.json()
    assert payload["requestId"]
    assert "secret connection" not in response.text
    with ledger.transaction() as ledger_conn:
        summary_row = ledger_conn.execute(
            "SELECT summary FROM brain_ops.turns WHERE request_id=%s", (payload["requestId"],)
        ).fetchone()
        assert summary_row is not None
        summary = summary_row["summary"]
    operations = ledger.operations()
    if scenario == "budget":
        assert response.status_code == 429
        assert payload["error"]["resetAt"] and payload["error"]["resources"]
        provider.create.assert_not_called()
    elif scenario == "provider":
        assert response.status_code >= 400
        assert provider.create.call_count == 1
        assert operations[0]["state"] == "uncertain"
        assert summary["unsettledNusd"] > 0
    elif scenario == "timeout":
        assert response.status_code == 504
        assert operations[0]["state"] == "uncertain"
        assert summary["unsettledNusd"] > 0
    else:
        assert response.status_code == 200
        assert provider.create.call_count == 1
        assert payload["metrics"]["reviewCalls"] == 0
        assert operations[0]["state"] == "settled"
        assert operations[0]["request_id"] == payload["requestId"]
        assert summary["costNusd"] == Usage(250, 0, 40, 0).cost(RELEASE.price)
        assert summary["toolResults"][0]["tool"] == "lookup_contact"
        if scenario == "supported":
            assert payload["status"] == "answered" and "201-684-7695" in payload["answer"]
            assert summary["toolResults"][0]["evidence_ids"] == [payload["citations"][0]["id"]]
        else:
            assert payload["status"] in {"clarification", "unavailable"}
            if scenario == "conflict":
                assert "conflicting" in payload["answer"]
            if scenario == "ambiguous":
                assert "more than one" in payload["answer"]
            assert "201-555-0199" not in payload["answer"]


def test_contact_discovery_uses_same_stemming_for_title_and_query(data: CampusData) -> None:
    result = data.search(SearchQuery(collection="contacts", query="library", limit=1))
    assert result["records"][0]["fields"]["name"] == "Library"
    assert all(not key.startswith("_") for key in result["records"][0])
