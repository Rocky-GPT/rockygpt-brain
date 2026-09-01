"""Proves the package imports and the ordered chat shell runs."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import rockygpt_brain
from rockygpt_brain.api.app import MODEL, ChatRequest, app, chat, health, readiness
from rockygpt_brain.transportation import (
    ShuttleClarificationRequest,
    ShuttleQuery,
    ShuttleQueryRequest,
    ShuttleResult,
    UpcomingDay,
)
from rockygpt_brain.transportation_interpretation import (
    INTERPRETATION_INSTRUCTIONS,
    SHUTTLE_TOOLS,
    TransportationInterpretation,
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
            "transportationResult": None,
            "transportationProvenance": None,
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


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "shuttle_schedule",
            json.dumps(
                {
                    "day": {
                        "day_kind": "upcoming",
                        "days_from_today": None,
                        "weekday": None,
                        "service_day": None,
                        "calendar_date": None,
                        "day_mention": None,
                    },
                    "mentions": [],
                    "show": "both",
                }
            ),
        ),
        ("shuttle_next_trips", "{not valid json"),
        (
            "shuttle_next_trips",
            json.dumps(
                {
                    "day": {
                        "day_kind": "upcoming",
                        "days_from_today": None,
                        "weekday": None,
                        "service_day": None,
                        "calendar_date": None,
                        "day_mention": None,
                    },
                    "mentions": [],
                    "count": 3,
                    "count_mention": "shuttle",
                    "offset": 0,
                    "offset_mention": "no_offset",
                    "show": "departure",
                }
            ),
        ),
        (
            "shuttle_availability",
            json.dumps(
                {
                    "mentions": [],
                    "relation": "at",
                    "clock": "00:00",
                    "clock_mention": "shuttle",
                    "basis": "arrival",
                }
            ),
        ),
        ("not_a_transportation_operation", "{}"),
    ],
)
def test_malformed_model_interpretation_never_causes_chat_5xx(
    tool_name: str, arguments: str
) -> None:
    response = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=tool_name,
                arguments=arguments,
            )
        ],
        output_text="",
        model="gpt-test",
    )

    with patch("rockygpt_brain.transportation_interpretation.OpenAI") as openai:
        openai.return_value.responses.create.return_value = response
        result = TestClient(app).post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Tell me about the shuttle."}]},
        )

    assert result.status_code == 200
    assert result.json()["transportationInterpretation"] == {
        "selected": True,
        "request": {
            "kind": "clarification",
            "reason": "interpretation_failure",
        },
        "model": "gpt-test",
    }


def test_unmatched_route_interpretation_is_repaired_before_execution() -> None:
    initial_request = ShuttleQueryRequest(
        kind="query",
        answer_kind="trips",
        query=ShuttleQuery(
            day=UpcomingDay(kind="upcoming"),
            selection="next",
            count=1,
            route_mention="shuttle",
        ),
        show="departure",
    )
    repaired_request = initial_request.model_copy(
        update={"query": initial_request.query.model_copy(update={"route_mention": None})}
    )
    initial = TransportationInterpretation(
        selected=True,
        request=initial_request,
        model="gpt-test",
    )
    repaired = TransportationInterpretation(
        selected=True,
        request=repaired_request,
        model="gpt-test",
    )
    result = Mock(
        request=repaired_request,
        provenance=None,
        model_dump=Mock(return_value={"outcome": "success"}),
    )

    with (
        patch(
            "rockygpt_brain.api.app.interpret_transportation",
            return_value=("", initial),
        ),
        patch(
            "rockygpt_brain.api.app.repair_transportation_interpretation",
            return_value=("", repaired),
        ) as repair,
        patch("rockygpt_brain.api.app.load_trusted_shuttle_data", return_value=Mock()) as load,
        patch(
            "rockygpt_brain.api.app.route_mentions_match_trusted_data",
            side_effect=[False, True],
        ),
        patch("rockygpt_brain.api.app.execute_transportation", return_value=result) as execute,
        patch("rockygpt_brain.api.app.answer_transportation", return_value="Grounded answer"),
    ):
        response = chat(
            ChatRequest.model_validate(
                {"messages": [{"role": "user", "content": "What time is the next shuttle?"}]}
            )
        )

    repair.assert_called_once()
    load.assert_called_once()
    execute.assert_called_once_with(repaired_request, data=load.return_value)
    assert response["answer"] == "Grounded answer"
    assert response["transportationInterpretation"] == repaired.model_dump(mode="json")


def test_failed_route_repair_becomes_typed_clarification_not_5xx() -> None:
    initial_request = ShuttleQueryRequest(
        kind="query",
        answer_kind="trips",
        query=ShuttleQuery(
            day=UpcomingDay(kind="upcoming"),
            selection="next",
            count=1,
            route_mention="shuttle",
        ),
        show="departure",
    )
    initial = TransportationInterpretation(
        selected=True,
        request=initial_request,
        model="gpt-test",
    )
    clarification = TransportationInterpretation(
        selected=True,
        request=ShuttleClarificationRequest(
            kind="clarification",
            reason="interpretation_failure",
        ),
        model="gpt-test",
    )
    assert isinstance(clarification.request, ShuttleClarificationRequest)
    clarification_result = ShuttleResult(
        outcome="needs_clarification",
        request=clarification.request,
        evaluated_at=datetime.fromisoformat("2026-08-31T12:00:00-04:00"),
    )

    with (
        patch(
            "rockygpt_brain.api.app.interpret_transportation",
            return_value=("", initial),
        ),
        patch(
            "rockygpt_brain.api.app.repair_transportation_interpretation",
            return_value=("", clarification),
        ),
        patch("rockygpt_brain.api.app.load_trusted_shuttle_data", return_value=Mock()),
        patch(
            "rockygpt_brain.api.app.route_mentions_match_trusted_data",
            return_value=False,
        ),
        patch(
            "rockygpt_brain.api.app.execute_transportation",
            return_value=clarification_result,
        ),
    ):
        response = TestClient(app).post(
            "/v1/chat",
            json={"messages": [{"role": "user", "content": "Next shuttle?"}]},
        )

    assert response.status_code == 200
    assert response.json()["transportationInterpretation"]["request"] == {
        "kind": "clarification",
        "reason": "interpretation_failure",
    }
