"""Proves the package imports and the minimal HTTP shell runs."""

from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from pydantic import ValidationError

import rockygpt_brain
from rockygpt_brain.api.app import MODEL, ChatRequest, chat, health, readiness
from rockygpt_brain.shuttle import next_shuttle

CAMPUS_TIME_ZONE = ZoneInfo("America/New_York")


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
    response = Mock(output_text="Hello from the model.", model="gpt-test")
    with patch("rockygpt_brain.api.app.OpenAI") as client:
        client.return_value.responses.create.return_value = response
        assert chat(ChatRequest.model_validate({"messages": messages})) == {
            "answer": "Hello from the model.",
            "model": "gpt-test",
        }

    client.return_value.responses.create.assert_called_once_with(
        model=MODEL,
        input=messages,
        store=False,
    )


def test_next_shuttle_merges_weekday_full_and_express_schedules() -> None:
    fact = next_shuttle(datetime(2026, 8, 31, 13, 37, tzinfo=CAMPUS_TIME_ZONE))

    assert fact["departureTime"] == "1:40 PM"
    assert fact["minutesUntil"] == 3
    assert fact["service"] == "Fall 2026 weekday train express"
    assert fact["method"] == "deterministic_schedule_lookup"
    assert fact["sourceUrl"] == (
        "https://www.ramapo.edu/about/transportation-services/"
        "shuttle-mid-day-weekday-express-train-schedule/"
    )


def test_next_shuttle_rolls_to_the_next_service_day() -> None:
    fact = next_shuttle(datetime(2026, 9, 4, 22, 0, tzinfo=CAMPUS_TIME_ZONE))

    assert fact["departureAt"] == "2026-09-05T09:00-04:00"
    assert fact["service"] == "Fall 2026 Saturday service"


def test_shuttle_question_sends_deterministic_fact_to_the_single_model_call() -> None:
    messages = [
        {"role": "user", "content": "Please remember I prefer concise answers."},
        {"role": "assistant", "content": "Okay."},
        {"role": "user", "content": "When is the next shuttle?"},
    ]
    fact = next_shuttle(datetime(2026, 8, 31, 13, 37, tzinfo=CAMPUS_TIME_ZONE))
    response = Mock(output_text="The next scheduled shuttle is at 1:40 PM.", model="gpt-test")

    with (
        patch("rockygpt_brain.api.app.next_shuttle", return_value=fact),
        patch("rockygpt_brain.api.app.OpenAI") as client,
    ):
        client.return_value.responses.create.return_value = response
        result = chat(ChatRequest.model_validate({"messages": messages}))

    assert result == {
        "answer": "The next scheduled shuttle is at 1:40 PM.",
        "model": "gpt-test",
        "shuttleFact": fact,
    }
    model_input = client.return_value.responses.create.call_args.kwargs["input"]
    assert model_input[1:] == messages
    assert '"departureTime":"1:40 PM"' in model_input[0]["content"]
    client.return_value.responses.create.assert_called_once()
