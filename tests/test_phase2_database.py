"""Actual PostgreSQL retrieval and HTTP/accounting path, with only the provider injected."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
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
from rockygpt_brain.data import CampusData, SearchQuery
from rockygpt_brain.exact import ContactQuery
from rockygpt_brain.provider import ModelResponse, OutputItem, PaidGateway, Usage
from test_accounting import database as database
from test_accounting import ledger as ledger


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
                    "office:registrar"
                    if scenario == "conflict"
                    else "office:other-registrar",
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
