"""Focused contract, conversation, and next-shuttle tests."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import rockygpt_brain
from rockygpt_brain.api.app import MODEL, ChatRequest, chat, health, readiness
from rockygpt_brain.shuttle import asks_for_next_shuttle, next_shuttle, render_next_shuttle_answer

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


@pytest.mark.parametrize(
    ("now", "departure_at", "minutes_until", "departure_day"),
    [
        (
            datetime(2026, 9, 4, 14, 54, tzinfo=CAMPUS_TIME_ZONE),
            "2026-09-04T14:55-04:00",
            1,
            "today",
        ),
        (
            datetime(2026, 9, 4, 14, 55, tzinfo=CAMPUS_TIME_ZONE),
            "2026-09-04T14:55-04:00",
            0,
            "today",
        ),
        (
            datetime(2026, 9, 4, 14, 56, tzinfo=CAMPUS_TIME_ZONE),
            "2026-09-04T15:25-04:00",
            29,
            "today",
        ),
        (
            datetime(2026, 9, 4, 21, 41, tzinfo=CAMPUS_TIME_ZONE),
            "2026-09-05T09:00-04:00",
            679,
            "tomorrow",
        ),
    ],
)
def test_next_shuttle_time_boundaries_use_controlled_current_time(
    now: datetime, departure_at: str, minutes_until: int, departure_day: str
) -> None:
    trips = [
        trip("2:55 PM", "weekday", "Ramsey Route 17", trip_id="current"),
        trip("3:25 PM", "weekday", "Ramsey Route 17", trip_id="after"),
        trip("9:40 PM", "weekday", "Weekday Roadrunner Express", trip_id="final"),
        trip("9:00 AM", "saturday", "Saturday Roadrunner Express", trip_id="tomorrow"),
    ]

    fact = next_shuttle(trips, now)

    assert fact["departureAt"] == departure_at
    assert fact["minutesUntil"] == minutes_until
    assert fact["departureDay"] == departure_day
    answer = render_next_shuttle_answer(fact)
    assert f"**{departure_day} at {fact['departureTime']}**" in answer
    if minutes_until == 0:
        assert "It is due now." in answer
    else:
        unit = "minute" if minutes_until == 1 else "minutes"
        assert f"**{minutes_until} {unit}**" in answer


@pytest.mark.parametrize(
    "question",
    [
        "When is the next shuttle?",
        "What time is the next shuttle?",
        "Is there another shuttle coming up?",
        "Is there another shuttle soon?",
        "Is a shuttle coming up?",
        "How soon is the next shuttle?",
        "When is the next shuttle from campus?",
        "Is the next shuttle from here coming soon?",
    ],
)
def test_immediate_next_shuttle_variants_are_in_scope(question: str) -> None:
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": question}]})
    assert asks_for_next_shuttle(request.messages)


@pytest.mark.parametrize(
    "question",
    [
        "When is the next shuttle tomorrow?",
        "Show me the next two shuttles.",
        "Where does the next shuttle go?",
        "What is the whole shuttle schedule?",
        "When was the last shuttle?",
        "Compare the next shuttle with the one after it.",
        "Is the next shuttle going to Ramsey?",
        "When is the next shuttle for Ramsey?",
        "What are the next shuttle times?",
        "When is the next shuttle at 5 PM?",
        "When do the shuttles run?",
        "When is the next shuttle from Ridgewood?",
        "When is the first shuttle today?",
    ],
)
def test_other_transportation_questions_do_not_receive_next_shuttle_fact(question: str) -> None:
    database_lookup = AsyncMock()
    response = Mock(output_text="Normal model response.", model="gpt-test")
    with (
        patch("rockygpt_brain.api.app.next_shuttle_from_database", new=database_lookup),
        patch("rockygpt_brain.api.app.OpenAI") as client,
    ):
        client.return_value.responses.create.return_value = response
        result = run_chat([{"role": "user", "content": question}])

    assert result == {"answer": "Normal model response.", "model": "gpt-test"}
    database_lookup.assert_not_awaited()


def test_trusted_fact_overrides_user_time_assumptions_without_model_rephrasing() -> None:
    messages = [
        {
            "role": "user",
            "content": "When is the next shuttle? I think it is right now and only 1 minute away.",
        }
    ]
    fact = next_shuttle(
        [trip("1:40 PM", "weekday", "Ramsey Route 17", trip_id="express")],
        datetime(2026, 8, 31, 13, 29, tzinfo=CAMPUS_TIME_ZONE),
    )

    with (
        patch(
            "rockygpt_brain.api.app.next_shuttle_from_database",
            new=AsyncMock(return_value=fact),
        ),
        patch("rockygpt_brain.api.app.OpenAI") as client,
    ):
        result = run_chat(messages)

    expected_answer = (
        "The next shuttle on **Ramsey Route 17** is scheduled to depart "
        "**today at 1:40 PM**. That is in **11 minutes**. "
        "Its scheduled arrival is **3:00 PM**."
    )
    assert render_next_shuttle_answer(fact) == expected_answer
    assert result == {"answer": expected_answer, "model": "deterministic", "shuttleFact": fact}
    answer = result["answer"]
    assert isinstance(answer, str)
    assert "right now" not in answer
    assert "**1 minute**" not in answer
    client.assert_not_called()


@pytest.mark.parametrize(
    ("now", "departure_at", "minutes_until"),
    [
        (
            datetime(2026, 9, 4, 14, 54, tzinfo=CAMPUS_TIME_ZONE),
            "2026-09-04T14:55-04:00",
            1,
        ),
        (
            datetime(2026, 9, 4, 14, 55, tzinfo=CAMPUS_TIME_ZONE),
            "2026-09-04T14:55-04:00",
            0,
        ),
        (
            datetime(2026, 9, 4, 14, 56, tzinfo=CAMPUS_TIME_ZONE),
            "2026-09-04T15:25-04:00",
            29,
        ),
        (
            datetime(2026, 9, 4, 21, 41, tzinfo=CAMPUS_TIME_ZONE),
            "2026-09-05T09:00-04:00",
            679,
        ),
    ],
)
def test_chat_time_boundaries_inject_clock_without_encoding_conditions_in_question(
    now: datetime, departure_at: str, minutes_until: int
) -> None:
    trips = [
        trip("2:55 PM", "weekday", "Ramsey Route 17", trip_id="current"),
        trip("3:25 PM", "weekday", "Ramsey Route 17", trip_id="after"),
        trip("9:40 PM", "weekday", "Weekday Roadrunner Express", trip_id="final"),
        trip("9:00 AM", "saturday", "Saturday Roadrunner Express", trip_id="tomorrow"),
    ]
    messages = [{"role": "user", "content": "When is the next shuttle?"}]

    with (
        patch("rockygpt_brain.api.app.campus_now", return_value=now),
        patch(
            "rockygpt_brain.shuttle.load_shuttle_trips",
            new=AsyncMock(return_value=trips),
        ),
        patch("rockygpt_brain.api.app.OpenAI") as client,
    ):
        result = run_chat(messages)

    fact = result["shuttleFact"]
    assert isinstance(fact, dict)
    assert fact["currentTime"] == now.isoformat(timespec="seconds")
    assert fact["departureAt"] == departure_at
    assert fact["minutesUntil"] == minutes_until
    assert result["answer"] == render_next_shuttle_answer(fact)
    client.assert_not_called()


def test_natural_follow_up_uses_actual_multi_turn_conversation() -> None:
    messages = [
        {"role": "user", "content": "Please remember I prefer concise answers."},
        {"role": "assistant", "content": "Okay."},
        {"role": "user", "content": "Is there another shuttle coming up?"},
    ]
    fact = next_shuttle(
        [trip("1:40 PM", "weekday", "Ramsey Route 17", trip_id="express")],
        datetime(2026, 8, 31, 13, 37, tzinfo=CAMPUS_TIME_ZONE),
    )
    now = datetime(2026, 8, 31, 13, 37, tzinfo=CAMPUS_TIME_ZONE)

    with (
        patch("rockygpt_brain.api.app.campus_now", return_value=now),
        patch(
            "rockygpt_brain.api.app.next_shuttle_from_database",
            new=AsyncMock(return_value=fact),
        ) as database_lookup,
        patch("rockygpt_brain.api.app.OpenAI") as client,
    ):
        result = run_chat(messages)

    assert result == {
        "answer": (
            "The next shuttle on **Ramsey Route 17** is scheduled to depart "
            "**today at 1:40 PM**. That is in **3 minutes**. "
            "Its scheduled arrival is **3:00 PM**."
        ),
        "model": "deterministic",
        "shuttleFact": fact,
    }
    database_lookup.assert_awaited_once_with(now)
    client.assert_not_called()


def test_out_of_scope_follow_up_keeps_actual_conversation_without_grounding() -> None:
    messages = [
        {"role": "user", "content": "When is the next shuttle?"},
        {"role": "assistant", "content": "The next shuttle is at 1:40 PM."},
        {"role": "user", "content": "What about tomorrow?"},
    ]
    response = Mock(output_text="Normal contextual response.", model="gpt-test")
    database_lookup = AsyncMock()
    with (
        patch("rockygpt_brain.api.app.next_shuttle_from_database", new=database_lookup),
        patch("rockygpt_brain.api.app.OpenAI") as client,
    ):
        client.return_value.responses.create.return_value = response
        result = run_chat(messages)

    assert result == {"answer": "Normal contextual response.", "model": "gpt-test"}
    database_lookup.assert_not_awaited()
    client.return_value.responses.create.assert_called_once_with(
        model=MODEL,
        input=messages,
        store=False,
    )


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
