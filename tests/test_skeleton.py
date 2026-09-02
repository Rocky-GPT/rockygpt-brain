"""Proves the HTTP shell and bounded capability classifier."""

import json
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import Request, Response
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
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
    represented = {
        capability for case in cases for capability in case["expected"]
    }
    assert represented == set(CAPABILITY_LABELS)
    assert any(case["name"].startswith("boundary_") for case in cases)
    assert any(case["name"].startswith("topic_switch_") for case in cases)
    assert any(case["name"].startswith("follow_up_") for case in cases)
    assert any(case["name"].startswith("ambiguous_") for case in cases)
    assert any(case["name"].startswith("multi_capability_") for case in cases)

    by_name = {case["name"]: case for case in cases}
    independent_ownership = {
        "ownership_shuttle_stops": ["transportation"],
        "ownership_event_location": ["events"],
        "boundary_dining_hours_not_general_hours": ["dining"],
        "boundary_location_not_directory": ["locations"],
        "boundary_directory_not_location": ["directory"],
        "ownership_technical_help_location": ["it_support"],
    }
    for name, expected in independent_ownership.items():
        assert len(by_name[name]["messages"]) == 1
        assert by_name[name]["expected"] == expected

    assert by_name["multi_capability_transportation_and_directory"]["expected"] == [
        "transportation",
        "directory",
    ]
    assert by_name["follow_up_event_location"]["expected"] == ["events"]
    assert by_name["follow_up_dining_place_hours"]["expected"] == ["dining"]
    assert by_name["follow_up_it_help_location"]["expected"] == ["it_support"]

    for case in cases:
        assert set(case) == {"name", "messages", "expected"}
        assert case["messages"]
        assert case["expected"]
        assert len(case["expected"]) == len(set(case["expected"]))
        assert all(label in CAPABILITY_LABELS for label in case["expected"])
        if "clarification" in case["expected"]:
            assert case["expected"] == ["clarification"]
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
def test_chat_returns_one_selected_label(label: str) -> None:
    response = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps({"capabilities": [label]}),
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
    assert result.json() == {
        "answer": label,
        "capabilities": [label],
        "model": "gpt-test",
    }


def test_chat_returns_multiple_labels_in_requested_order() -> None:
    response = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps(
                    {"capabilities": ["transportation", "dining"]}
                ),
            )
        ],
        model="gpt-test",
    )
    with patch("rockygpt_brain.capabilities.classifier.OpenAI") as openai:
        openai.return_value.responses.create.return_value = response
        result = TestClient(app).post(
            "/v1/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "When is the next shuttle and what is for lunch?",
                    }
                ]
            },
        )

    assert result.json() == {
        "answer": "transportation\n\ndining",
        "capabilities": ["transportation", "dining"],
        "model": "gpt-test",
    }


def test_duplicate_labels_are_removed_without_reordering() -> None:
    response = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps(
                    {"capabilities": ["locations", "directory", "locations"]}
                ),
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

    assert result.json()["capabilities"] == ["locations", "directory"]


def test_clarification_is_never_combined_with_another_label() -> None:
    response = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps(
                    {"capabilities": ["dining", "clarification"]}
                ),
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

    assert result.json() == {
        "answer": "clarification",
        "capabilities": ["clarification"],
        "model": "gpt-test",
    }


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
                arguments=json.dumps({"capabilities": ["directory"]}),
            )
        ],
        model="gpt-test",
    )
    with patch("rockygpt_brain.capabilities.classifier.OpenAI") as openai:
        openai.return_value.responses.create.return_value = response
        result = TestClient(app).post("/v1/chat", json={"messages": messages})

    assert result.json() == {
        "answer": "directory",
        "capabilities": ["directory"],
        "model": "gpt-test",
    }
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
    openai.assert_called_once_with(max_retries=0, timeout=90.0)


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
                arguments=json.dumps({"capabilities": ["unknown"]}),
            )
        ],
        [
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps({"capabilities": ["dining"]}),
            ),
            SimpleNamespace(
                type="function_call",
                name=CLASSIFIER_TOOL_NAME,
                arguments=json.dumps({"capabilities": ["hours"]}),
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
    assert result.json() == {
        "answer": "clarification",
        "capabilities": ["clarification"],
        "model": "gpt-test",
    }


def test_upstream_rate_limit_is_reasoned_429_without_retry() -> None:
    upstream = Response(
        429,
        request=Request("POST", "https://api.openai.com/v1/responses"),
        json={"error": {"code": "rate_limit_exceeded"}},
    )
    error = RateLimitError(
        "Rate limit reached",
        response=upstream,
        body={"code": "rate_limit_exceeded"},
    )

    with patch("rockygpt_brain.api.app.classify", side_effect=error) as classify:
        result = TestClient(app).post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Classify this"}]},
        )

    assert result.status_code == 429
    assert result.json() == {
        "error": "The classifier model is temporarily rate limited.",
        "reason": "rate_limited",
        "detail": "No classification was produced. Try this request again later.",
        "retryable": True,
    }
    classify.assert_called_once()


@pytest.mark.parametrize(
    ("error", "status_code", "reason"),
    [
        (
            APITimeoutError(request=Request("POST", "https://api.openai.com/v1/responses")),
            504,
            "model_timeout",
        ),
        (
            APIConnectionError(
                request=Request("POST", "https://api.openai.com/v1/responses")
            ),
            503,
            "model_unreachable",
        ),
        (
            APIStatusError(
                "Provider failed",
                response=Response(
                    500,
                    request=Request("POST", "https://api.openai.com/v1/responses"),
                ),
                body=None,
            ),
            502,
            "model_provider_error",
        ),
    ],
)
def test_upstream_failures_have_reasoned_responses_without_retry(
    error: Exception, status_code: int, reason: str
) -> None:
    with patch("rockygpt_brain.api.app.classify", side_effect=error) as classify:
        result = TestClient(app).post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Classify this"}]},
        )

    assert result.status_code == status_code
    body = result.json()
    assert body["reason"] == reason
    assert body["detail"].startswith("No classification was produced")
    assert isinstance(body["retryable"], bool)
    classify.assert_called_once()
