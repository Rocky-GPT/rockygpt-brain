"""Proves the package imports and the ordered chat shell runs."""

from unittest.mock import Mock, patch

from pydantic import ValidationError

import rockygpt_brain
from rockygpt_brain.api.app import MODEL, ChatRequest, chat, health, readiness
from rockygpt_brain.transportation_interpretation import (
    INTERPRETATION_INSTRUCTIONS,
    SHUTTLE_TOOLS,
)


def test_package_imports() -> None:
    assert rockygpt_brain.__version__ == "0.0.0"


def test_health() -> None:
    assert health() == {"status": "ok"}


def test_readiness() -> None:
    assert readiness() == {"status": "ready"}


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
        try:
            ChatRequest.model_validate(payload)
        except ValidationError:
            continue
        raise AssertionError(f"request should have been rejected: {payload}")


def test_chat_passes_messages_to_openai_in_order() -> None:
    messages = [
        {"role": "user", "content": "My name is Sam."},
        {"role": "assistant", "content": "Hello, Sam."},
        {"role": "user", "content": "What is my name?"},
    ]
    response = Mock(output=[], output_text="Hello from the model.", model="gpt-test")
    with patch("rockygpt_brain.transportation_interpretation.OpenAI") as client:
        client.return_value.responses.create.return_value = response
        assert chat(ChatRequest.model_validate({"messages": messages})) == {
            "answer": "Hello from the model.",
            "model": "gpt-test",
            "transportationInterpretation": {
                "selected": False,
                "request": None,
                "model": "gpt-test",
            },
        }

    client.return_value.responses.create.assert_called_once_with(
        model=MODEL,
        input=messages,
        instructions=INTERPRETATION_INSTRUCTIONS,
        tools=SHUTTLE_TOOLS,
        tool_choice="auto",
        parallel_tool_calls=False,
        store=False,
        temperature=0,
    )
