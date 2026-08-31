"""Proves the package imports and the minimal HTTP shell runs."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import rockygpt_brain
from rockygpt_brain.api.app import MODEL, ChatRequest, chat, health, readiness
from rockygpt_brain.shuttle import next_shuttle

CAMPUS_TIME_ZONE = ZoneInfo("America/New_York")


def run_chat(messages: list[dict[str, str]]) -> dict[str, object]:
    return asyncio.run(chat(ChatRequest.model_validate({"messages": messages})))


def trip(
    departure: str,
    service_day: str,
    route: str,
    *,
    trip_id: str = "trip-1",
) -> dict[str, object]:
    return {
        "trip_id": trip_id,
        "source_record_key": f"{route}:{departure}",
        "departure": departure,
        "arrival": "3:00 PM",
        "collected_at": datetime(2026, 8, 28, 21, 22, tzinfo=UTC),
        "valid_from": None,
        "valid_until": None,
        "content_hash": "trusted-row-hash",
        "route_name": route,
        "service_day": service_day,
        "dataset_version": "v2-active",
        "dataset_activated_at": datetime(2026, 8, 31, 18, 2, tzinfo=UTC),
        "source_title": "Transportation Services",
        "source_url": "https://www.ramapo.edu/about/transportation-services/",
        "source_trust_tier": "official_primary",
    }


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
        assert run_chat(messages) == {
            "answer": "Hello from the model.",
            "model": "gpt-test",
        }

    client.return_value.responses.create.assert_called_once_with(
        model=MODEL,
        input=messages,
        store=False,
    )


def test_next_shuttle_chooses_earliest_trip_from_active_database_rows() -> None:
    trips = [
        trip("2:05 PM", "weekday", "Weekday Roadrunner Express", trip_id="full"),
        trip("1:40 PM", "weekday", "Ramsey Route 17", trip_id="express"),
    ]
    fact = next_shuttle(trips, datetime(2026, 8, 31, 13, 37, tzinfo=CAMPUS_TIME_ZONE))

    assert fact["departureTime"] == "1:40 PM"
    assert fact["minutesUntil"] == 3
    assert fact["route"] == "Ramsey Route 17"
    assert fact["tripId"] == "express"
    assert fact["datasetVersion"] == "v2-active"
    assert fact["sourceTrustTier"] == "official_primary"
    assert fact["method"] == "deterministic_database_schedule_lookup"


def test_next_shuttle_rolls_to_the_next_service_day() -> None:
    trips = [
        trip("9:40 PM", "weekday", "Weekday Roadrunner Express"),
        trip("9:00 AM", "saturday", "Saturday Roadrunner Express", trip_id="saturday"),
    ]
    fact = next_shuttle(trips, datetime(2026, 9, 4, 22, 0, tzinfo=CAMPUS_TIME_ZONE))

    assert fact["departureAt"] == "2026-09-05T09:00-04:00"
    assert fact["tripId"] == "saturday"


def test_shuttle_question_sends_deterministic_fact_to_the_single_model_call() -> None:
    messages = [
        {"role": "user", "content": "Please remember I prefer concise answers."},
        {"role": "assistant", "content": "Okay."},
        {"role": "user", "content": "When is the next shuttle?"},
    ]
    fact = next_shuttle(
        [trip("1:40 PM", "weekday", "Ramsey Route 17", trip_id="express")],
        datetime(2026, 8, 31, 13, 37, tzinfo=CAMPUS_TIME_ZONE),
    )
    response = Mock(output_text="The next scheduled shuttle is at 1:40 PM.", model="gpt-test")

    with (
        patch(
            "rockygpt_brain.api.app.next_shuttle_from_database",
            new=AsyncMock(return_value=fact),
        ) as database_lookup,
        patch("rockygpt_brain.api.app.OpenAI") as client,
    ):
        client.return_value.responses.create.return_value = response
        result = run_chat(messages)

    assert result == {
        "answer": "The next scheduled shuttle is at 1:40 PM.",
        "model": "gpt-test",
        "shuttleFact": fact,
    }
    model_input = client.return_value.responses.create.call_args.kwargs["input"]
    assert model_input[1:] == messages
    assert '"departureTime":"1:40 PM"' in model_input[0]["content"]
    database_lookup.assert_awaited_once_with()
    client.return_value.responses.create.assert_called_once()


def test_shuttle_database_failure_never_falls_back_to_model_knowledge() -> None:
    with (
        patch(
            "rockygpt_brain.api.app.next_shuttle_from_database",
            new=AsyncMock(side_effect=RuntimeError("database unavailable")),
        ),
        patch("rockygpt_brain.api.app.OpenAI") as client,
        pytest.raises(HTTPException) as error,
    ):
        run_chat([{"role": "user", "content": "When is the next shuttle?"}])

    assert error.value.status_code == 503
    client.assert_not_called()
