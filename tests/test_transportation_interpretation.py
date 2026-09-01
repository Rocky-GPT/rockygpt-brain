"""Focused tests for model-to-contract shuttle interpretation."""

import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from rockygpt_brain.transportation import (
    RelativeDay,
    ServiceDayTemplate,
    ShuttleClarificationRequest,
    ShuttleComparisonRequest,
    ShuttleQueryRequest,
    ShuttleRequest,
    UnsupportedShuttleRequest,
)
from rockygpt_brain.transportation_interpretation import (
    AVAILABILITY_TOOL_NAME,
    CLARIFICATION_TOOL_NAME,
    COMPARISON_TOOL_NAME,
    INTERPRETATION_FAILURE_ANSWER,
    INTERPRETATION_INSTRUCTIONS,
    NEXT_TRIPS_TOOL_NAME,
    SCHEDULE_TOOL_NAME,
    SHUTTLE_TOOLS,
    UNSUPPORTED_TOOL_NAME,
    ConversationMessage,
    InvalidTransportationInterpretation,
    TransportationInterpretation,
    interpret_transportation,
    validate_tool_arguments,
)


def day_to_wire(day_value: object) -> dict[str, object]:
    day = cast(dict[str, Any], day_value)
    return {
        "day_kind": day["kind"],
        "days_from_today": day.get("days_from_today"),
        "weekday": day.get("weekday"),
        "service_day": day.get("service_day"),
        "calendar_date": day.get("date"),
    }


def lookup_to_wire(query_value: object) -> dict[str, object]:
    query = cast(dict[str, Any], query_value)
    mentions = [
        {"role": role, "text": query[field]}
        for role, field in (
            ("route", "route_mention"),
            ("origin", "origin_mention"),
            ("destination", "destination_mention"),
        )
        if query.get(field) is not None
    ]
    return {
        "day": day_to_wire(query["day"]),
        "mentions": mentions,
    }


def request_to_wire(request_value: dict[str, object]) -> dict[str, object]:
    kind = request_value["kind"]
    if kind == "query":
        query = cast(dict[str, Any], request_value["query"])
        wire = lookup_to_wire(query)
        if request_value["answer_kind"] == "availability":
            constraint = cast(dict[str, Any], query["time"])
            return {
                **wire,
                "relation": constraint["relation"],
                "clock": constraint["clock"],
                "basis": constraint["basis"],
            }
        if query["selection"] == "next":
            return {
                **wire,
                "count": query["count"],
                "offset": query.get("offset", 0),
                "show": request_value["show"],
            }
        return {**wire, "show": request_value["show"]}
    if kind == "comparison":
        queries = cast(list[object], request_value["queries"])
        return {
            "queries": [lookup_to_wire(query) for query in queries],
            "show": request_value["show"],
        }
    if kind == "clarification":
        return {"reason": request_value["reason"]}
    return {"reason": request_value["reason"]}


def tool_name_for(request_value: dict[str, object]) -> str:
    kind = request_value["kind"]
    if kind == "query":
        query = cast(dict[str, Any], request_value["query"])
        if request_value["answer_kind"] == "availability":
            return AVAILABILITY_TOOL_NAME
        if query["selection"] == "next":
            return NEXT_TRIPS_TOOL_NAME
        return SCHEDULE_TOOL_NAME
    return {
        "comparison": COMPARISON_TOOL_NAME,
        "clarification": CLARIFICATION_TOOL_NAME,
        "unsupported": UNSUPPORTED_TOOL_NAME,
    }[cast(str, kind)]


def next_wire_arguments(count: int | None) -> dict[str, object]:
    return {
        "day": {
            "day_kind": "upcoming",
            "days_from_today": None,
            "weekday": None,
            "service_day": None,
            "calendar_date": None,
        },
        "count": count,
        "offset": 0,
        "mentions": [],
        "show": "both",
    }


def tool_response(request: dict[str, object]) -> Mock:
    return Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=tool_name_for(request),
                arguments=json.dumps(request_to_wire(request)),
            )
        ],
        output_text="",
        model="gpt-test",
    )


def text_response(answer: str = "Normal chat answer.") -> Mock:
    return Mock(output=[], output_text=answer, model="gpt-test")


def interpret(
    messages: list[ConversationMessage], response: Mock
) -> tuple[str, TransportationInterpretation, Mock]:
    with patch("rockygpt_brain.transportation_interpretation.OpenAI") as client:
        client.return_value.responses.create.return_value = response
        answer, interpretation = interpret_transportation(messages, "gpt-test")
    return answer, interpretation, client


NATURAL_VARIANTS: list[tuple[str, dict[str, object]]] = [
    (
        "Is there another campus bus coming up?",
        {
            "kind": "query",
            "answer_kind": "trips",
            "query": {
                "day": {"kind": "upcoming"},
                "selection": "next",
                "count": 1,
                "offset": 0,
                "route_mention": None,
                "origin_mention": "campus",
                "destination_mention": None,
                "time": None,
            },
            "show": "both",
        },
    ),
    (
        "Could I get the next three shuttle departures?",
        {
            "kind": "query",
            "answer_kind": "trips",
            "query": {
                "day": {"kind": "upcoming"},
                "selection": "next",
                "count": 3,
                "offset": 0,
                "route_mention": None,
                "origin_mention": None,
                "destination_mention": None,
                "time": None,
            },
            "show": "departure",
        },
    ),
    (
        "Please show every shuttle tomorrow.",
        {
            "kind": "query",
            "answer_kind": "trips",
            "query": {
                "day": {"kind": "relative", "days_from_today": 1},
                "selection": "all",
                "count": None,
                "offset": 0,
                "route_mention": None,
                "origin_mention": None,
                "destination_mention": None,
                "time": None,
            },
            "show": "both",
        },
    ),
    (
        "Does a shuttle arrive around 5 PM today?",
        {
            "kind": "query",
            "answer_kind": "availability",
            "query": {
                "day": {"kind": "relative", "days_from_today": 0},
                "selection": "all",
                "count": None,
                "offset": 0,
                "route_mention": None,
                "origin_mention": None,
                "destination_mention": None,
                "time": {"relation": "around", "clock": "17:00", "basis": "arrival"},
            },
            "show": "arrival",
        },
    ),
    (
        "How do the Saturday and Sunday shuttle schedules compare?",
        {
            "kind": "comparison",
            "queries": [
                {
                    "day": {"kind": "service_day", "service_day": "saturday"},
                    "selection": "all",
                    "count": None,
                    "offset": 0,
                    "route_mention": None,
                    "origin_mention": None,
                    "destination_mention": None,
                    "time": None,
                },
                {
                    "day": {"kind": "service_day", "service_day": "sunday"},
                    "selection": "all",
                    "count": None,
                    "offset": 0,
                    "route_mention": None,
                    "origin_mention": None,
                    "destination_mention": None,
                    "time": None,
                },
            ],
            "show": "both",
        },
    ),
]


@pytest.mark.parametrize(("question", "request_payload"), NATURAL_VARIANTS)
def test_natural_wording_variations_validate_as_exact_step_5a_requests(
    question: str, request_payload: dict[str, object]
) -> None:
    messages: list[ConversationMessage] = [{"role": "user", "content": question}]
    answer, interpretation, client = interpret(messages, tool_response(request_payload))
    expected = ShuttleRequest.model_validate(request_payload).root

    assert answer == ""
    assert interpretation.selected is True
    assert interpretation.request == expected
    client.return_value.responses.create.assert_called_once_with(
        model="gpt-test",
        input=messages,
        instructions=INTERPRETATION_INSTRUCTIONS,
        tools=SHUTTLE_TOOLS,
        tool_choice="auto",
        parallel_tool_calls=False,
        store=False,
        temperature=0,
    )


def test_ambiguous_shuttle_wording_produces_clarification_request() -> None:
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "Can you help with the shuttle thing?"}
    ]
    response = tool_response({"kind": "clarification", "reason": "ambiguous_request"})

    _, interpretation, _ = interpret(messages, response)

    assert isinstance(interpretation.request, ShuttleClarificationRequest)
    assert interpretation.request.reason == "ambiguous_request"


def test_supported_route_origin_destination_and_offset_arguments_are_preserved() -> None:
    question = "After the next Ramsey Route 17 shuttle from campus to the station, what follows?"
    messages: list[ConversationMessage] = [{"role": "user", "content": question}]
    request: dict[str, object] = {
        "kind": "query",
        "answer_kind": "trips",
        "query": {
            "day": {"kind": "upcoming"},
            "selection": "next",
            "count": 1,
            "offset": 1,
            "route_mention": "Ramsey Route 17",
            "origin_mention": "campus",
            "destination_mention": "the station",
            "time": None,
        },
        "show": "both",
    }

    _, interpretation, _ = interpret(messages, tool_response(request))

    assert isinstance(interpretation.request, ShuttleQueryRequest)
    assert interpretation.request.query.offset == 1
    assert interpretation.request.query.route_mention == "Ramsey Route 17"
    assert interpretation.request.query.origin_mention == "campus"
    assert interpretation.request.query.destination_mention == "the station"


def test_requested_place_is_a_destination_not_a_route() -> None:
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "When is the next shuttle to Ridgewood?"}
    ]
    request: dict[str, object] = {
        "kind": "query",
        "answer_kind": "trips",
        "query": {
            "day": {"kind": "upcoming"},
            "selection": "next",
            "count": 1,
            "offset": 0,
            "route_mention": None,
            "origin_mention": None,
            "destination_mention": "Ridgewood",
            "time": None,
        },
        "show": "both",
    }

    _, interpretation, _ = interpret(messages, tool_response(request))

    assert isinstance(interpretation.request, ShuttleQueryRequest)
    assert interpretation.request.query.route_mention is None
    assert interpretation.request.query.destination_mention == "Ridgewood"


def test_station_arrival_question_produces_a_valid_arrival_request() -> None:
    messages: list[ConversationMessage] = [
        {
            "role": "user",
            "content": "What time does the next shuttle arrive at the train station?",
        }
    ]
    request: dict[str, object] = {
        "kind": "query",
        "answer_kind": "trips",
        "query": {
            "day": {"kind": "upcoming"},
            "selection": "next",
            "count": 1,
            "offset": 0,
            "route_mention": None,
            "origin_mention": None,
            "destination_mention": "the train station",
            "time": None,
        },
        "show": "arrival",
    }

    _, interpretation, _ = interpret(messages, tool_response(request))

    assert isinstance(interpretation.request, ShuttleQueryRequest)
    assert interpretation.request.show == "arrival"
    assert interpretation.request.query.destination_mention == "the train station"


def test_availability_without_explicit_day_defaults_to_today() -> None:
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "Is there a shuttle at 1 PM?"}
    ]
    response = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=AVAILABILITY_TOOL_NAME,
                arguments=json.dumps(
                    {
                        "day": None,
                        "mentions": [],
                        "relation": "at",
                        "clock": "13:00",
                        "basis": "departure",
                    }
                ),
            )
        ],
        output_text="",
        model="gpt-test",
    )

    _, interpretation, _ = interpret(messages, response)

    assert isinstance(interpretation.request, ShuttleQueryRequest)
    assert interpretation.request.answer_kind == "availability"
    assert interpretation.request.query.day == RelativeDay(
        kind="relative", days_from_today=0
    )


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        ("Is the campus shuttle delayed right now?", "live_status"),
        ("Can I reserve a seat on the shuttle?", "booking"),
        ("Will the shuttle run on Thanksgiving if campus closes?", "service_exception"),
    ],
)
def test_unsupported_shuttle_requests_are_explicit(question: str, reason: str) -> None:
    messages: list[ConversationMessage] = [{"role": "user", "content": question}]
    _, interpretation, _ = interpret(
        messages,
        tool_response({"kind": "unsupported", "reason": reason}),
    )

    assert isinstance(interpretation.request, UnsupportedShuttleRequest)
    assert interpretation.request.reason == reason


def test_non_shuttle_request_is_an_explicit_non_selection_with_normal_chat_answer() -> None:
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "Help me outline an essay about plate tectonics."}
    ]

    answer, interpretation, _ = interpret(messages, text_response())

    assert answer == "Normal chat answer."
    assert interpretation.model_dump() == {
        "selected": False,
        "request": None,
        "model": "gpt-test",
    }


def test_actual_multi_turn_follow_up_preserves_order_and_resolves_context() -> None:
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "Show me the Saturday shuttle schedule."},
        {
            "role": "assistant",
            "content": "Shuttle request interpreted. Schedule execution is not available yet.",
        },
        {"role": "user", "content": "What about Sunday?"},
    ]
    request: dict[str, object] = {
        "kind": "query",
        "answer_kind": "trips",
        "query": {
            "day": {"kind": "service_day", "service_day": "sunday"},
            "selection": "all",
            "count": None,
            "offset": 0,
            "route_mention": None,
            "origin_mention": None,
            "destination_mention": None,
            "time": None,
        },
        "show": "both",
    }

    _, interpretation, client = interpret(messages, tool_response(request))

    assert isinstance(interpretation.request, ShuttleQueryRequest)
    assert isinstance(interpretation.request.query.day, ServiceDayTemplate)
    assert interpretation.request.query.day.service_day == "sunday"
    assert client.return_value.responses.create.call_args.kwargs["input"] == messages


def test_comparison_output_is_the_preserved_step_5a_variant() -> None:
    question, request = NATURAL_VARIANTS[-1]
    messages: list[ConversationMessage] = [{"role": "user", "content": question}]

    _, interpretation, _ = interpret(messages, tool_response(request))

    assert isinstance(interpretation.request, ShuttleComparisonRequest)
    assert len(interpretation.request.queries) == 2


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (NEXT_TRIPS_TOOL_NAME, {**next_wire_arguments(1), "trip_id": "invented-trip"}),
        (NEXT_TRIPS_TOOL_NAME, next_wire_arguments(None)),
        (
            AVAILABILITY_TOOL_NAME,
            {
                "day": cast(dict[str, object], next_wire_arguments(1)["day"]),
                "mentions": [],
                "relation": "around",
                "clock": "17:00Z",
                "basis": "arrival",
            },
        ),
    ],
)
def test_exact_output_validation_rejects_facts_and_invalid_combinations(
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "When is the next shuttle?"}
    ]
    with pytest.raises(InvalidTransportationInterpretation):
        validate_tool_arguments(tool_name, json.dumps(arguments), messages)


def test_model_cannot_invent_route_origin_or_destination_mentions() -> None:
    arguments = request_to_wire(
        {
            "kind": "query",
            "answer_kind": "trips",
            "query": {
                "day": {"kind": "upcoming"},
                "selection": "next",
                "count": 1,
                "offset": 0,
                "route_mention": "Invented Express",
                "origin_mention": None,
                "destination_mention": None,
                "time": None,
            },
            "show": "both",
        }
    )
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "When is the next shuttle?"}
    ]

    with pytest.raises(InvalidTransportationInterpretation, match="user text"):
        validate_tool_arguments(NEXT_TRIPS_TOOL_NAME, json.dumps(arguments), messages)


def test_tool_schema_is_strict_and_contains_no_shuttle_fact_fields() -> None:
    serialized_schema = json.dumps([tool["parameters"] for tool in SHUTTLE_TOOLS])

    assert all(tool["strict"] is True for tool in SHUTTLE_TOOLS)
    assert "trip_id" not in serialized_schema
    assert "source_record_key" not in serialized_schema
    assert "content_hash" not in serialized_schema
    assert "minutes_until" not in serialized_schema
    assert "ShuttleTripFact" not in serialized_schema
    assert "oneOf" not in serialized_schema
    assert "prefixItems" not in serialized_schema


def test_bounded_tools_cannot_represent_an_upcoming_full_schedule() -> None:
    bounded_tool_names = {
        SCHEDULE_TOOL_NAME,
        AVAILABILITY_TOOL_NAME,
        COMPARISON_TOOL_NAME,
    }

    for tool in SHUTTLE_TOOLS:
        if tool["name"] in bounded_tool_names:
            assert "upcoming" not in json.dumps(tool["parameters"])


def test_multiple_tool_calls_produce_a_safe_interpretation_failure() -> None:
    response = tool_response({"kind": "clarification", "reason": "ambiguous_request"})
    response.output.append(response.output[0])
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "Tell me about the shuttle."}
    ]

    answer, interpretation, _ = interpret(messages, response)

    assert answer == INTERPRETATION_FAILURE_ANSWER
    assert interpretation.request == ShuttleClarificationRequest(
        kind="clarification", reason="interpretation_failure"
    )
