"""Structural eligibility for the general-answer exemption; semantic eval is separate."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from test_engine import NOW, RECORD, answer, review, search, tools

SAFETY = [
    {"id": f"critical_facts:{key}", "title": "Campus Facts",
     "url": "https://www.ramapo.edu/publicsafety/", "collection": "critical_facts",
     "freshness": "static", "fields": {"fact_key": key, "fact_value": value}}
    for key, value in [("printing.free_pages_per_academic_year", "400"),
                       ("safety.non_emergency_phone", "201-684-7432"),
                       ("safety.emergency_phone", "201-684-6666")]
]
DANGER = "I'm on campus and someone is following me. How do I get help right now?"
GUIDANCE = "Call 911 now and move toward a busy, staffed place."


def urgent(text: str) -> SimpleNamespace:
    response = answer(text, "guidance")
    payload = json.loads(response.output_text)
    payload["general_scope"] = "urgent_safety"
    response.output_text = json.dumps(payload)
    return response


def test_urgent_safety_adds_verified_public_safety_numbers_without_another_model_call() -> None:
    client, data = Mock(), Mock()
    client.create.return_value = urgent(GUIDANCE)
    data.search.return_value = {"status": "ok", "dataset_version": "v1", "records": SAFETY}
    result = run_turn([ChatMessage(role="user", content=DANGER)],
                      client=client, data=data, model="test", now=NOW)
    assert client.create.call_count == 1
    assert result["metrics"]["responseMode"] == "urgent_safety"
    assert result["metrics"]["reviewCalls"] == 0
    guidance, numbers = result["answer"].split("\n\n")
    assert guidance == GUIDANCE
    assert numbers.startswith(
        "Ramapo College Public Safety: emergency 201-684-6666; non-emergency 201-684-7432.")
    assert [citation["id"] for citation in result["citations"]] == [
        "critical_facts:safety.emergency_phone", "critical_facts:safety.non_emergency_phone"]
    assert result["datasetVersion"] == "v1"
    [call] = data.search.call_args_list
    assert call.args[0].collection == "critical_facts"


def test_urgent_safety_guidance_stands_when_campus_numbers_are_unavailable() -> None:
    client, data = Mock(), Mock()
    client.create.return_value = urgent(GUIDANCE)
    data.search.side_effect = RuntimeError("Unavailable")
    result = run_turn([ChatMessage(role="user", content=DANGER)],
                      client=client, data=data, model="test", now=NOW)
    assert result["answer"] == GUIDANCE
    assert result["citations"] == []
    assert result["metrics"]["responseMode"] == "general"
    assert client.create.call_count == 1


@pytest.mark.parametrize(
    ("question", "text", "scope", "status", "kind"),
    [
        ("hey", "Hey! How can I help?", "conversation", "answered", "guidance"),
        (
            "Explain recursion",
            "Recursion solves a problem using smaller instances of itself.",
            "stable_explanation",
            "answered",
            "guidance",
        ),
        ("Help me study", "Try spaced practice and self-testing.", "study", "answered", "guidance"),
        (
            "Draft a thank-you note",
            "Thank you for your time and help.",
            "writing",
            "answered",
            "guidance",
        ),
        (
            "When does it close?",
            "Which place do you mean?",
            "clarification",
            "clarification",
            "clarification",
        ),
    ],
)
def test_general_or_clarification_uses_one_call(
    question: str,
    text: str,
    scope: str,
    status: str,
    kind: str,
) -> None:
    client, data = Mock(), Mock()
    response = answer(text, kind, status=status)
    payload = json.loads(response.output_text)
    payload["general_scope"] = scope
    response.output_text = json.dumps(payload)
    client.create.return_value = response
    result = run_turn(
        [ChatMessage(role="user", content=question)],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["answer"] == text
    assert result["status"] == status
    assert result["metrics"]["responseMode"] == "general"
    assert result["metrics"]["reviewCalls"] == 0
    assert client.create.call_count == 1
    data.search.assert_not_called()
    schema = client.create.call_args.kwargs["text"]["format"]["schema"]
    assert set(schema["required"]) == set(schema["properties"])


def test_general_label_cannot_bypass_review_of_retrieved_campus_content() -> None:
    client, data = Mock(), Mock()
    response = answer("The library is D-224.", "guidance", [RECORD["id"]])
    payload = json.loads(response.output_text)
    payload["general_scope"] = "writing"
    response.output_text = json.dumps(payload)
    client.create.side_effect = [tools(search()), response, review("wrong_scope")]
    data.search.return_value = {"status": "ok", "records": [RECORD]}
    result = run_turn(
        [ChatMessage(role="user", content="Put the library location in my note")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["status"] == "unavailable"
    assert "D-224" not in result["answer"]
    assert result["metrics"]["reviewCalls"] == 1


def test_failed_retrieval_cannot_be_reclassified_as_general_success() -> None:
    client, data = Mock(), Mock()
    response = answer("The library is open all night.", "guidance")
    payload = json.loads(response.output_text)
    payload["general_scope"] = "stable_explanation"
    response.output_text = json.dumps(payload)
    client.create.side_effect = [tools(search()), response, review("unsupported_claim")]
    data.search.side_effect = RuntimeError("Unavailable")
    result = run_turn(
        [ChatMessage(role="user", content="When is the library open?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["status"] == "unavailable"
    assert "open all night" not in result["answer"]
    assert result["metrics"]["reviewCalls"] == 1
