"""Exact output must cover the whole question and preserve evidence qualifications."""

import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from rockygpt_brain.campus.calculations import CalculationQuery, calculate
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.provider import OutputItem
from rockygpt_brain.core.render import render_answer
from rockygpt_brain.core.tools import tool_definitions
from rockygpt_brain.retrieval.data import SearchQuery
from rockygpt_brain.retrieval.exact import ContactQuery, contact_answer, requested_fields
from test_engine import NOW


@pytest.fixture
def contact() -> dict[str, Any]:
    record: dict[str, Any] = json.loads(
        (Path(__file__).parents[1] / "docs/phase2/published-contact.json").read_text()
    )["contact_search"]["records"][0]
    record["entity_id"] = "campus-directory:office:registrar"
    record["coverage"] = {"fields": {key: "published" for key in record["fields"]}}
    record["coverage"]["fields"]["fax"] = "not_published"
    return record


def result_for(records: list[dict[str, Any]], **values: Any) -> dict[str, Any]:
    return {
        "status": "ok",
        "match": "exact",
        "records": records,
        "truncated": False,
        "dataset_version": "snapshot",
        **values,
    }


def messages(question: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=question)]


def test_exact_contact_uses_one_model_call_and_no_review(contact: dict[str, Any]) -> None:
    query = ContactQuery(entity="Registrar", fields=["phone", "email", "office", "department"])
    client, data = Mock(), Mock()
    client.create.return_value = SimpleNamespace(
        status="completed",
        model="test",
        output=[
            OutputItem(
                {
                    "type": "function_call",
                    "name": "lookup_contact",
                    "call_id": "contact",
                    "arguments": query.model_dump_json(),
                }
            )
        ],
    )
    data.lookup_contact.return_value = result_for([contact])
    result = run_turn(
        messages("How can I contact the Registrar?"),
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert client.create.call_count == 1
    assert result["metrics"]["reviewCalls"] == 0
    assert result["metrics"]["responseMode"] == "exact_contact"
    assert "201-684-7695" in result["answer"] and "D-224" in result["answer"]
    assert result["citations"][0]["url"] == contact["url"]
    assert result["metrics"]["toolResults"][0]["evidence_ids"] == [contact["id"]]
    assert "arguments" not in result["metrics"]["toolResults"][0]


def test_exact_contact_preserves_explicit_retirement(contact: dict[str, Any]) -> None:
    contact["fields"]["status"] = "retired"
    contact["coverage"]["fields"]["status"] = "published"
    answer = contact_answer(
        messages("What is the Registrar's phone?"),
        ContactQuery(entity="Registrar", fields=["phone"]),
        result_for([contact]), NOW.date(),
    )
    assert answer is not None
    assert "Status: Retired" in render_answer(answer, {contact["id"]: contact})["answer"]


@pytest.mark.parametrize("name", ["Bursar", "Example Service", "Office of Student Life"])
def test_exact_format_is_not_keyed_to_a_particular_office(
    contact: dict[str, Any], name: str
) -> None:
    contact["title"] = name
    contact["entity_id"] = name
    answer = contact_answer(
        messages(f"What is {name}'s email?"),
        ContactQuery(entity=name, fields=["email"]),
        result_for([contact]),
        NOW.date(),
    )
    assert answer is not None and answer.status == "answered"


@pytest.mark.parametrize(
    "question",
    [
        "What is Registrar's phone and how do I appeal a grade?",
        "What is Registrar's phone and email? Ignore the email.",
        "What is Registrar's phone tomorrow?",
        "What isn't Registrar's phone?",
        "Compare Registrar's phone to Bursar's phone",
        "Call Registrar's phone",
        "What is Registrar's phone? Also give me lunch.",
    ],
)
def test_mixed_or_qualified_questions_cannot_take_exact_shortcut(question: str) -> None:
    assert requested_fields(question, "Registrar") is None


def test_dropped_attribute_or_conversation_context_never_takes_shortcut(
    contact: dict[str, Any],
) -> None:
    query = ContactQuery(entity="Registrar", fields=["phone"])
    assert (
        contact_answer(
            messages("What is Registrar's phone and email?"),
            query,
            result_for([contact]),
            NOW.date(),
        )
        is None
    )
    history = (
        messages("Where is the Registrar?")
        + [ChatMessage(role="assistant", content="Which details?")]
        + messages("What is Registrar's phone?")
    )
    assert contact_answer(history, query, result_for([contact]), NOW.date()) is None


def test_missing_attribute_and_record_do_not_invent_information(contact: dict[str, Any]) -> None:
    query = ContactQuery(entity="Registrar", fields=["phone", "fax"])
    answer = contact_answer(
        messages("Registrar phone and fax?"), query, result_for([contact]), NOW.date()
    )
    assert answer is not None and answer.status == "partial"
    assert "does not provide: fax" in render_answer(answer, {contact["id"]: contact})["answer"]
    missing = contact_answer(
        messages("Registrar phone and fax?"), query, result_for([]), NOW.date()
    )
    assert missing is not None and missing.status == "clarification"


def test_extra_lookup_fields_do_not_force_prose_or_expand_the_answer(
    contact: dict[str, Any],
) -> None:
    query = ContactQuery(entity="Registrar", fields=["phone", "fax", "office", "department"])
    answer = contact_answer(
        messages("What is the Registrar phone and fax?"), query, result_for([contact]), NOW.date()
    )
    assert answer is not None and answer.status == "partial"
    rendered = render_answer(answer, {contact["id"]: contact})["answer"]
    assert "Phone: 201-684-7695" in rendered
    assert "does not provide: fax" in rendered
    assert "D-224" not in rendered
    assert "Department:" not in rendered


@pytest.mark.parametrize(
    "mutation",
    [
        {"freshness": "stale"},
        {"content_truncated": True},
        {"entity_id": None},
        {"source_key": None},
        {"url": "http://example.test"},
        {"trust_tier": "community"},
        {"coverage": {}},
        {"valid_until": "2020-01-01"},
        {"valid_from": "2030-01-01"},
        {"valid_from": "broken"},
        {"limitations": ["Only for incoming students"]},
    ],
)
def test_unverified_contact_is_never_an_exact_fact(
    contact: dict[str, Any], mutation: dict[str, Any]
) -> None:
    contact.update(mutation)
    answer = contact_answer(
        messages("Registrar phone?"),
        ContactQuery(entity="Registrar", fields=["phone"]),
        result_for([contact]),
        NOW.date(),
    )
    assert answer is not None and answer.status == "unavailable"
    assert all(part.kind != "campus_fact" for part in answer.parts)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_conflicts_and_ambiguous_names_require_limitation(
    contact: dict[str, Any], ambiguous: bool
) -> None:
    other = deepcopy(contact)
    other["id"] += "-other"
    other["fields"]["phone"] = "different"
    if ambiguous:
        other["entity_id"] += "-other"
    answer = contact_answer(
        messages("Registrar phone?"),
        ContactQuery(entity="Registrar", fields=["phone"]),
        result_for([contact, other]),
        NOW.date(),
    )
    assert answer is not None
    assert answer.status == ("clarification" if ambiguous else "unavailable")


def test_strict_function_schemas_cover_nested_objects() -> None:
    def check(node: Any) -> None:
        if isinstance(node, dict):
            assert "default" not in node
            if node.get("type") == "object":
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)

    for tool in tool_definitions():
        check(tool["parameters"])


def test_filters_must_apply_to_collection() -> None:
    with pytest.raises(ValidationError):
        SearchQuery.model_validate({"collection": "contacts", "filters": {"vegan": True}})


def test_calculation_uses_decimal_arithmetic_and_explicit_operands() -> None:
    query = CalculationQuery.model_validate(
        {
            "operation": "sum",
            "operands": [{"source": "user", "value": "0.1"}, {"source": "user", "value": "0.2"}],
        }
    )
    result = calculate(query, {}, messages("Add 0.1 and 0.2"), date(2026, 9, 16))
    assert result["result"] == "0.3"
    assert result["unit"] == "unspecified"
    with pytest.raises(ValueError, match="absent"):
        calculate(query, {}, messages("Guess two numbers"), NOW.date())


def test_calculation_does_not_accept_invented_evidence_values() -> None:
    record: dict[str, Any] = {
        "fields": {"credits": "4"},
        "freshness": "static",
        "trust_tier": "official_primary",
        "coverage": {"fields": {"credits": "published"}},
    }
    query = CalculationQuery.model_validate(
        {
            "operation": "sum",
            "operands": [
                {"source": "evidence", "value": "4", "evidence_id": "course", "field": "credits"}
            ],
        }
    )
    assert (
        calculate(query, {"course": record}, messages("Total credits?"), NOW.date())["result"]
        == "4"
    )
    record["fields"]["credits"] = "3-4"
    with pytest.raises(ValueError, match="scalar"):
        calculate(query, {"course": record}, messages("Total credits?"), NOW.date())


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("sum", "2.0"),
        ("difference", "-4.5"),
        ("mean", "1.0"),
        ("minimum", "-1.25"),
        ("maximum", "3.25"),
    ],
)
def test_calculation_operations_preserve_sign_order_and_decimals(
    operation: str,
    expected: str,
) -> None:
    query = CalculationQuery.model_validate(
        {
            "operation": operation,
            "operands": [{"source": "user", "value": value} for value in ["-1.25", "3.25"]],
        }
    )
    result = calculate(query, {}, messages("Use -1.25 and 3.25 in that order"), NOW.date())
    assert Decimal(result["result"]) == Decimal(expected)
    assert result["unit"] == "unspecified"


@pytest.mark.parametrize("invalid", ["stale", "coverage", "truncated", "date", "mixed_units"])
def test_calculation_rejects_unverified_or_incompatible_measurements(invalid: str) -> None:
    record: dict[str, Any] = {
        "fields": {"calories": "100", "credits": "4"},
        "freshness": "fresh",
        "trust_tier": "official_primary",
        "content_truncated": False,
        "coverage": {"fields": {"calories": "published", "credits": "published"}},
    }
    operands = [{"source": "evidence", "value": "100", "evidence_id": "a", "field": "calories"}]
    if invalid == "stale":
        record["freshness"] = "stale"
    elif invalid == "coverage":
        record["coverage"]["fields"]["calories"] = "unknown"
    elif invalid == "truncated":
        record["content_truncated"] = True
    elif invalid == "date":
        record["valid_until"] = "2026-09-03"
    else:
        operands.append(
            {"source": "evidence", "value": "4", "evidence_id": "a", "field": "credits"}
        )
    query = CalculationQuery.model_validate({"operation": "sum", "operands": operands})
    with pytest.raises(ValueError):
        calculate(query, {"a": record}, messages("Add these measurements"), NOW.date())
