"""Actual PostgreSQL retrieval and HTTP/accounting path, with only the provider injected."""

import hashlib
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
from psycopg.types.json import Jsonb

from rockygpt_brain.api.app import app
from rockygpt_brain.config import MONTHLY_CAP_NUSD, RELEASE, Deployment
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.provider import ModelResponse, OutputItem, PaidGateway, Usage, input_bound
from rockygpt_brain.governance.accounting import PostgresLedger
from rockygpt_brain.governance.evidence import map_references, reference_aliases
from rockygpt_brain.retrieval.data import CampusData, SearchQuery
from rockygpt_brain.retrieval.exact import ContactQuery
from test_accounting import database as database
from test_accounting import ledger as ledger
from test_engine import tools
from test_evidence import expand_records

REGISTRAR = "0458a32a-e19c-49e9-8170-1a08a17d1658"
# The capture predates the identity registry, and contacts resolve only through a
# canonical entity since 5e8260e. This is the Registrar entry the data publisher
# compiles today, minus its building relationship (no building is loaded here).
REGISTRY = {
    "schema_version": 1,
    "entities": [
        {
            "id": REGISTRAR,
            "kind": "office",
            "name": "Registrar",
            "aliases": ["Office of the Registrar"],
            "links": [
                {
                    "collection": "contacts",
                    "source_key": "campus-directory",
                    "source_record_keys": ["office:registrar"],
                }
            ],
        }
    ],
}


@pytest.fixture(scope="module")
def frozen(database: str) -> dict[str, Any]:
    loaded = load_snapshot()
    payload = json.dumps(REGISTRY, sort_keys=True, separators=(",", ":"))
    with psycopg.connect(local_database()) as conn:
        conn.execute(
            "INSERT INTO rockygpt_v2.release_artifacts "
            "(dataset_version_id,artifact_key,payload,content_hash) VALUES (%s,%s,%s,%s)",
            (
                loaded["dataset"]["id"],
                "campus-identities",
                Jsonb(REGISTRY),
                hashlib.sha256(payload.encode()).hexdigest(),
            ),
        )
    return loaded


@pytest.fixture
def data(frozen: dict[str, Any]) -> Iterator[CampusData]:
    repository = CampusData(local_database(), datetime.fromisoformat(frozen["captured_at"]))
    yield repository
    repository.close()


def test_actual_contact_sql_pins_identity_aliases_and_fields(data: CampusData) -> None:
    query = ContactQuery(entity="Office of the Registrar", fields=["phone", "email", "fax"])
    output = data.lookup_contact(query)
    assert output["resolution"]["entity"]["id"] == REGISTRAR
    assert len(output["records"]) == 1
    record = output["records"][0]
    assert record["entity_id"] == "campus-directory:office:registrar"
    assert record["fields"]["phone"] == "201-684-7695"
    assert output["field_status"] == {"phone": "known", "email": "known", "fax": "unknown"}
    assert not output["truncated"]
    injected = data.lookup_contact(ContactQuery(entity="' OR true --", fields=["phone"]))
    assert injected["resolution"]["status"] == "no_match" and injected["records"] == []
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


OVERSIZED_DELIVERY = (
    "Since c6fe234, bounded delivery omits the oversized evidence (0 of 50 menu and 0 of 4 "
    "hours records) and still makes the answer call, where this case expects "
    "retrieval_context_limit before a second paid call. Decide which behavior is intended."
)


@pytest.mark.parametrize(
    "oversized",
    [False, pytest.param(True, marks=pytest.mark.xfail(strict=True, reason=OVERSIZED_DELIVERY))],
)
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
    # The capture's 51st dinner row is the "Have a Nice Day" sign-off, which menu
    # retrieval has dropped as non-food since e05f461; 50 real items remain.
    assert [len(result["records"]) for result in expected] == [50, 4]
    assert expected[0]["total_matches"] == 50 and not expected[0]["truncated"]
    messages = [
        {"role": "user", "content": "hey"},
        {"role": "assistant", "content": "Hey! How can I help with Ramapo today?"},
        {"role": "user", "content": "what is for dinner today"},
    ]
    menu, hours = [result["records"] for result in expected]
    # Bounded delivery (c6fe234) may send only a prefix of the menu. The simulated
    # model, like a real one, cites only the records it actually received.
    sent: dict[str, Any] = {}
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
            # bounded compact delivery fits, with no inflated token cap.
            assert input_bound({**payload, "input": legacy_history}) > RELEASE.max_input_tokens
            assert input_bound(payload) <= RELEASE.max_input_tokens
            raw = [
                json.loads(item["output"])
                for item in kwargs["input"]
                if item.get("type") == "function_call_output"
            ]
            sent["menu"] = menu[: len(expand_records(raw[0]["evidence_groups"]))]
            assert sent["menu"], "some dinner evidence must reach the model"
            sent["aliases"] = {
                alias: record_id
                for record_id, alias in reference_aliases(
                    [r["id"] for r in sent["menu"] + hours]
                ).items()
            }
            outputs = [map_references(output, sent["aliases"]) for output in raw]
            for result, original, records in zip(
                outputs, expected, (sent["menu"], hours), strict=True
            ):
                # Delivered records are an unaltered prefix; omissions are explicit.
                assert expand_records(result.pop("evidence_groups")) == records
                metadata = {k: v for k, v in original.items() if k != "records"}
                if len(records) < len(original["records"]):
                    metadata.update(
                        truncated=True,
                        reason="retrieval_delivery_limit",
                        retrieved_count=len(original["records"]),
                        omitted_count=len(original["records"]) - len(records),
                    )
                assert result == metadata
            sent["parts"] = [
                {
                    "kind": "campus_fact",
                    "text": "Dinner menu: " + ", ".join(r["title"] for r in sent["menu"]),
                    "evidence_ids": [r["id"] for r in sent["menu"]],
                },
                {
                    "kind": "campus_fact",
                    "text": "Birch Tree Inn lists dinner from 5 to 8 PM.",
                    "evidence_ids": [r["id"] for r in hours if r["title"] == "Birch Tree Inn"],
                },
                {
                    "kind": "limitation",
                    "text": f"These are {len(sent['menu'])} of {len(menu)} matching items, "
                    "not the full menu.",
                    "evidence_ids": [],
                },
            ]
            text = json.dumps({"status": "partial", "parts": sent["parts"]})
        else:
            assert len(calls) == 3
            review_input = map_references(json.loads(kwargs["input"]), sent["aliases"])
            assert review_input["conversation"] == messages
            assert expand_records(review_input["evidence"]) == sent["menu"] + hours
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
                        for i in range(len(sent["parts"]))
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
        assert all(r["title"] in payload["answer"] for r in sent["menu"])
        assert len(payload["citations"]) == len(sent["menu"]) + 1  # Plus Birch Tree Inn hours.
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


def text_response(value: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        "fixture-" + str(uuid4()),
        RELEASE.model,
        "completed",
        json.dumps(value),
        [],
        Usage(250, 0, 40, 0),
    )


def draft(status: str, kind: str, text: str, *evidence_ids: str) -> dict[str, Any]:
    return {
        "status": status,
        "parts": [{"kind": kind, "text": text, "evidence_ids": list(evidence_ids)}],
    }


SUPPORTED = {
    "parts": [
        {
            "part_index": 0,
            "verdict": "supported",
            "reason": "",
            "unverified_premises": [],
            "uses_event_for_entity": False,
            "infers_food_safety": False,
            "plan_deadlines": [],
        }
    ]
}


@pytest.mark.parametrize(
    "scenario",
    [
        "supported",
        "missing",
        "uncovered",
        "unlinked_alias",
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
    registrar = next(
        f"contacts:{row['id']}"
        for row in frozen["tables"]["campus_contacts"]
        if row["source_record_key"] == "office:registrar"
    )
    phone = "The Registrar's phone is 201-684-7695."
    # Contacts resolve through the canonical entity since 5e8260e, so their answers
    # are drafted from the delivered entity facts and reviewed: three paid calls.
    drafts = {
        "supported": draft("answered", "campus_fact", phone, registrar),
        "missing": draft("clarification", "clarification", "Which office do you mean?"),
        "uncovered": draft(
            "unavailable", "limitation", "The directory doesn't publish a fax number."
        ),
        "unlinked_alias": draft("answered", "campus_fact", phone, registrar),
        "conflict": draft(
            "unavailable", "limitation", "The directory lists conflicting phone numbers."
        ),
    }
    entity, fields, question = "Registrar", ["phone"], "What is Registrar's phone?"
    if scenario == "missing":
        entity, question = "Unknown Office", "What is Unknown Office's phone?"
    if scenario == "uncovered":
        fields, question = ["fax"], "What is Registrar's fax?"
    injected_id = None
    if scenario in {"unlinked_alias", "conflict"}:
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
        entity, question = "Office of the Registrar", "What is Office of the Registrar's phone?"
    calls: list[dict[str, Any]] = []
    delivered: list[Any] = []

    def create(**kwargs: Any) -> ModelResponse:
        calls.append(kwargs)
        if len(calls) == 1:
            return provider_response(entity, fields)
        if len(calls) == 2:
            delivered.extend(
                json.loads(item["output"])
                for item in kwargs["input"]
                if item.get("type") == "function_call_output"
            )
            return text_response(drafts[scenario])
        return text_response(SUPPORTED)

    provider = Mock()
    if scenario in drafts:
        provider.create.side_effect = create
    else:
        provider.create.return_value = provider_response()
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
    elif scenario == "database":
        assert response.status_code == 200
        assert provider.create.call_count == 1
        assert payload["metrics"]["reviewCalls"] == 0
        assert payload["status"] == "unavailable"
        assert operations[0]["state"] == "settled"
        assert operations[0]["request_id"] == payload["requestId"]
        assert summary["costNusd"] == Usage(250, 0, 40, 0).cost(RELEASE.price)
        assert summary["toolResults"][0]["tool"] == "lookup_contact"
    else:
        assert response.status_code == 200
        assert len(calls) == len(operations) == 3
        assert payload["metrics"]["reviewCalls"] == 1
        assert all(op["state"] == "settled" for op in operations)
        assert all(op["request_id"] == payload["requestId"] for op in operations)
        assert summary["costNusd"] == 3 * Usage(250, 0, 40, 0).cost(RELEASE.price)
        result = summary["toolResults"][0]
        assert result["tool"] == "lookup_contact"
        wire = json.dumps(delivered)
        if scenario in {"supported", "unlinked_alias"}:
            # A directory row that only shares the alias is not linked, so it is never read.
            assert result["resolution"]["entity"]["id"] == REGISTRAR
            assert result["evidence_ids"] == [payload["citations"][0]["id"]] == [registrar]
            assert payload["status"] == "answered" and "201-684-7695" in payload["answer"]
            assert "201-555-0199" not in wire
        if scenario == "missing":
            assert result["resolution"]["status"] == "no_match"
            assert result["evidence_ids"] == []
            assert payload["status"] == "clarification"
        if scenario == "uncovered":
            assert delivered[0]["field_status"] == {"fax": "unknown"}
            assert payload["status"] == "unavailable"
        if scenario == "conflict":
            phones = next(p for p in result["entity_facts"]["properties"] if p["key"] == "phones")
            assert phones["status"] == "conflicting"
            assert "201-684-7695" in wire and "201-555-0199" in wire
            assert payload["status"] == "unavailable"


def test_contact_discovery_uses_same_stemming_for_title_and_query(data: CampusData) -> None:
    result = data.search(SearchQuery(collection="contacts", query="library", limit=1))
    assert result["records"][0]["fields"]["name"] == "Library"
    assert all(not key.startswith("_") for key in result["records"][0])


def test_documents_rank_passages_by_how_much_of_the_request_they_cover(data: CampusData) -> None:
    # Catalog passages repeat "course"; the section covering every word comes first.
    result = data.search(
        SearchQuery(collection="documents", query="withdraw from a course deadline", limit=4)
    )
    assert result["records"][0]["title"].endswith("Withdrawal (Online Courses Only)")
    assert not any("Course Catalog" in record["title"] for record in result["records"])
    # Among passages covering the same words, the one whose heading names them leads.
    form = data.search(SearchQuery(collection="documents", query="declare major form", limit=1))
    assert form["records"][0]["title"].endswith("Major/Minor Declaration Form")


DOCUMENT_QUERIES = [
    "withdraw from a course deadline", "declare major form", "Potter Library location",
    "financial aid office", "library hours", "counseling appointment", "parking permit",
    "meal plan", "transcript request", "tuition or fees", "switch dorm rooms", "Ramapo College",
    "student", "the", "", "zzzxqy",
    "financial aid FAFSA scholarships grants loans tuition bill refund",
]


@contextmanager
def _documents_edited(dataset_id: str) -> Iterator[None]:
    """Passages without a heading path, Counseling passages whose heading path omits their
    title, the Housing document under a community source, and a retired release holding a
    copy of every document; all undone afterwards."""
    retired, community = uuid4(), uuid4()
    with psycopg.connect(local_database(), autocommit=True) as conn:
        saved = conn.execute("SELECT id, metadata FROM rockygpt_v2.document_chunks").fetchall()
        housing = conn.execute(
            "SELECT id, source_id FROM rockygpt_v2.documents "
            "WHERE dataset_version_id = %s AND title = 'Housing'",
            (dataset_id,),
        ).fetchone()
        assert housing is not None
        try:
            conn.execute(
                "UPDATE rockygpt_v2.document_chunks SET metadata = metadata - 'headingPath' "
                "WHERE mod(chunk_index, 3) = 0 "
                "OR document_id = (SELECT id FROM rockygpt_v2.documents ORDER BY id LIMIT 1)"
            )
            conn.execute(
                "UPDATE rockygpt_v2.document_chunks c "
                "SET metadata = c.metadata || '{\"headingPath\": \"Overview\"}' "
                "FROM rockygpt_v2.documents d "
                "WHERE d.id = c.document_id AND d.title = 'Ramapo Counseling Services'"
            )
            conn.execute(
                "INSERT INTO rockygpt_v2.sources "
                "(id, source_key, title, canonical_url, trust_tier, freshness_sla_hours, domain) "
                "VALUES (%s, %s, 'Community notes', 'https://example.org/', 'community', 24, "
                "'housing')",
                (community, f"community-{community}"),
            )
            conn.execute(
                "UPDATE rockygpt_v2.documents SET source_id = %s WHERE id = %s",
                (community, housing[0]),
            )
            conn.execute(
                "INSERT INTO rockygpt_v2.dataset_versions (id, version, status) "
                "VALUES (%s, %s, 'retired')",
                (retired, f"retired-copy-{retired}"),
            )
            conn.execute(
                "INSERT INTO rockygpt_v2.documents "
                "(id, dataset_version_id, source_id, title, content, metadata, collected_at) "
                "SELECT md5(id::text || 'copy')::uuid, %s, source_id, title, content, metadata, "
                "collected_at FROM rockygpt_v2.documents WHERE dataset_version_id = %s",
                (retired, dataset_id),
            )
            conn.execute(
                "INSERT INTO rockygpt_v2.document_chunks "
                "(document_id, chunk_index, content, content_hash, metadata) "
                "SELECT md5(c.document_id::text || 'copy')::uuid, c.chunk_index, c.content, "
                "c.content_hash, c.metadata FROM rockygpt_v2.document_chunks c "
                "JOIN rockygpt_v2.documents d ON d.id = c.document_id "
                "WHERE d.dataset_version_id = %s",
                (dataset_id,),
            )
            yield
        finally:
            conn.execute("DELETE FROM rockygpt_v2.dataset_versions WHERE id = %s", (retired,))
            conn.execute(
                "UPDATE rockygpt_v2.documents SET source_id = %s WHERE id = %s",
                (housing[1], housing[0]),
            )
            conn.execute("DELETE FROM rockygpt_v2.sources WHERE id = %s", (community,))
            with conn.cursor() as cursor:
                cursor.executemany(
                    "UPDATE rockygpt_v2.document_chunks SET metadata = %s WHERE id = %s",
                    [(Jsonb(metadata), chunk_id) for chunk_id, metadata in saved],
                )


def test_documents_rank_the_same_with_and_without_the_heading_path_index(
    data: CampusData, frozen: dict[str, Any]
) -> None:
    # With rockygpt-data's heading path index the search finds matching passages through
    # indexes; without it, every heading vector is built. Both must list the same passages,
    # also after edits the snapshot lacks: passages matched by their document's title,
    # heading paths that omit the title, a community document to leave out, and another
    # release's copy of every passage in the same tables.
    def compare() -> list[dict[str, Any]]:
        scanned: list[dict[str, Any]] = []
        for query in DOCUMENT_QUERIES:
            # The scanning order (weight, score, id) is total, so its top 40 holds every
            # smaller list as a prefix.
            data._has_heading_path_index = False
            full = data.search(SearchQuery(collection="documents", query=query, limit=40))
            data._has_heading_path_index = True
            for limit in (1, 6, 40):
                indexed = data.search(SearchQuery(collection="documents", query=query, limit=limit))
                if limit == 40:
                    assert indexed == full, query
                else:
                    assert indexed["records"] == full["records"][:limit], (query, limit)
                    assert indexed["total_matches"] == full["total_matches"], (query, limit)
            scanned.append(full)
        return scanned

    data.search(SearchQuery(collection="documents", query="library", limit=1))
    assert data._has_heading_path_index is True
    snapshot = compare()
    assert sum(len(result["records"]) for result in snapshot) > 200
    with _documents_edited(frozen["dataset"]["id"]):
        edited = compare()
    assert edited != snapshot


def test_documents_weigh_rare_words_and_the_ones_a_heading_names(data: CampusData) -> None:
    # Events held at the library also say "location"; the library's own entry names it.
    library = data.search(
        SearchQuery(collection="documents", query="Potter Library location", limit=4)
    )
    assert library["records"][0]["title"].endswith("› Potter Library")
    aid = data.search(SearchQuery(collection="documents", query="financial aid office", limit=1))
    assert aid["records"][0]["title"].endswith("› Financial Aid")
