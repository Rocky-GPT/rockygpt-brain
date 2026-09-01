"""Proves the HTTP shell and bounded capability classifier."""

import json
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import rockygpt_brain
from rockygpt_brain.api.app import MODEL, ChatRequest, app, health, readiness
from rockygpt_brain.capabilities.classifier import (
    CAPABILITY_LABELS,
    CLASSIFIER_INSTRUCTIONS,
    CLASSIFIER_TOOL,
    CLASSIFIER_TOOL_NAME,
)


def test_package_imports() -> None:
    assert rockygpt_brain.__version__ == "0.0.0"


def test_health() -> None:
    assert health() == {"status": "ok"}


def test_readiness() -> None:
    assert readiness() == {"status": "ready"}


def test_complete_initial_label_set_is_exact() -> None:
    assert CAPABILITY_LABELS == (
        "transportation",
        "dining",
        "events",
        "hours",
        "directory",
        "locations",
        "courses",
        "programs",
        "clubs",
        "academic_calendar",
        "campus_documents",
        "student_services",
        "it_support",
        "personal_account",
        "general",
        "clarification",
    )


def test_classifier_prompt_is_loaded_from_its_own_file() -> None:
    prompt = (
        files("rockygpt_brain.capabilities")
        .joinpath("prompt.md")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert CLASSIFIER_INSTRUCTIONS == prompt
    assert "select_capability" in prompt


def test_fixed_evaluation_dataset_is_isolated_and_complete() -> None:
    dataset_path = Path(__file__).parents[1] / "evals" / "capability_classifier.json"
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))

    assert len(cases) == len({case["name"] for case in cases})
    assert {case["expected"] for case in cases} == set(CAPABILITY_LABELS)
    assert any(case["name"].startswith("boundary_") for case in cases)
    assert any(case["name"].startswith("topic_switch_") for case in cases)
    assert any(case["name"].startswith("follow_up_") for case in cases)
    assert any(case["name"].startswith("ambiguous_") for case in cases)
    assert any(case["name"].startswith("multi_capability_") for case in cases)

    for case in cases:
        assert set(case) == {"name", "messages", "expected"}
        assert case["messages"]
        assert case["messages"][-1]["role"] == "user"
        for message in case["messages"]:
            assert set(message) == {"role", "content"}
            assert message["role"] in {"user", "assistant"}
            assert isinstance(message["content"], str) and message["content"]


def test_chat_request_has_only_ordered_role_content_messages() -> None:
    request = ChatRequest.model_validate(
        {
            "messages": [
                {"role": "user", "content": "First"},
                {"role": "assistant", "content": "Second"},
                {"role": "user", "content": "Third"},
            ]
        }
    )
    assert request.model_dump() == {
        "messages": [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Second"},
            {"role": "user", "content": "Third"},
        ]
    }


def test_chat_rejects_legacy_or_extra_request_fields() -> None:
    for payload in (
        {"message": "legacy"},
        {"messages": [{"role": "user", "content": "Hello", "extra": True}]},
    ):
        with pytest.raises(ValidationError):
            ChatRequest.model_validate(payload)


@pytest.mark.parametrize("label", CAPABILITY_LABELS)
def test_chat_returns_only_selected_label_as_answer(label: str) -> None:
    response = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps({"capability": label}),
            )
        ],
        model="gpt-test",
    )
    with patch("rockygpt_brain.capabilities.classifier.OpenAI") as openai:
        openai.return_value.responses.create.return_value = response
        result = TestClient(app).post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Classify this"}]},
        )

    assert result.status_code == 200
    assert result.json() == {"answer": label, "model": "gpt-test"}


def test_chat_passes_complete_conversation_to_one_constrained_model_call() -> None:
    messages = [
        {"role": "user", "content": "Where is the registrar?"},
        {"role": "assistant", "content": "directory"},
        {"role": "user", "content": "What room is it in?"},
    ]
    response = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps({"capability": "directory"}),
            )
        ],
        model="gpt-test",
    )
    with patch("rockygpt_brain.capabilities.classifier.OpenAI") as openai:
        openai.return_value.responses.create.return_value = response
        result = TestClient(app).post("/v1/chat", json={"messages": messages})

    assert result.json() == {"answer": "directory", "model": "gpt-test"}
    openai.return_value.responses.create.assert_called_once_with(
        model=MODEL,
        input=messages,
        instructions=CLASSIFIER_INSTRUCTIONS,
        tools=[CLASSIFIER_TOOL],
        tool_choice={"type": "function", "name": CLASSIFIER_TOOL_NAME},
        parallel_tool_calls=False,
        store=False,
        temperature=0,
    )


@pytest.mark.parametrize(
    "output",
    [
        [],
        [
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments="{not json",
            )
        ],
        [
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps({"capability": "unknown"}),
            )
        ],
        [
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps({"capability": "dining"}),
            ),
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps({"capability": "hours"}),
            ),
        ],
    ],
)
def test_malformed_model_output_safely_becomes_clarification(output: list[object]) -> None:
    response = Mock(output=output, model="gpt-test")
    with patch("rockygpt_brain.capabilities.classifier.OpenAI") as openai:
        openai.return_value.responses.create.return_value = response
        result = TestClient(app).post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "What?"}]},
        )

    assert result.status_code == 200
    assert result.json() == {"answer": "clarification", "model": "gpt-test"}
