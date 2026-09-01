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
    DATED_AVAILABILITY_TOOL_NAME,
    INTERPRETATION_FAILURE_ANSWER,
    INTERPRETATION_INSTRUCTIONS,
    LAST_TRIP_TOOL_NAME,
    NEXT_TRIP_TOOL_NAME,
    NEXT_TRIPS_TOOL_NAME,
    RETRY_INSTRUCTIONS,
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
    day_mention = {
        "upcoming": None,
        "relative": "today" if day.get("days_from_today") == 0 else "tomorrow",
        "named_weekday": day.get("weekday"),
        "service_day": day.get("service_day"),
        "calendar_date": day.get("date"),
    }[cast(str, day["kind"])]
    return {
        "day_kind": day["kind"],
        "days_from_today": day.get("days_from_today"),
        "weekday": day.get("weekday"),
        "service_day": day.get("service_day"),
        "calendar_date": day.get("date"),
        "day_mention": day_mention,
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
                "clock_mention": "5 PM" if constraint["clock"] == "17:00" else "1 PM",
                "basis": constraint["basis"],
            }
        if query["selection"] == "next":
            count = cast(int, query["count"])
            count_mentions = {2: "two", 3: "three"}
            wire = {
                **wire,
                "offset": query.get("offset") or None,
                "offset_mention": "what follows" if query.get("offset") else None,
                "show": request_value["show"],
            }
            if count > 1:
                wire.update({"count": count, "count_mention": count_mentions[count]})
            return wire
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
            return DATED_AVAILABILITY_TOOL_NAME
        if query["selection"] == "next":
            return NEXT_TRIPS_TOOL_NAME if query["count"] > 1 else NEXT_TRIP_TOOL_NAME
        return SCHEDULE_TOOL_NAME
    return {
        "comparison": COMPARISON_TOOL_NAME,
        "clarification": CLARIFICATION_TOOL_NAME,
        "unsupported": UNSUPPORTED_TOOL_NAME,
    }[cast(str, kind)]


def next_wire_arguments(count: int | None) -> dict[str, object]:
    arguments: dict[str, object] = {
        "day": {
            "day_kind": "upcoming",
            "days_from_today": None,
            "weekday": None,
            "service_day": None,
            "calendar_date": None,
            "day_mention": None,
        },
        "offset": None,
        "offset_mention": None,
        "mentions": [],
        "show": "both",
    }
    if count is not None and count > 1:
        arguments.update({"count": count, "count_mention": "three"})
    return arguments


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
        "When is the shuttle tomorrow?",
        {
            "kind": "query",
            "answer_kind": "trips",
            "query": {
                "day": {"kind": "relative", "days_from_today": 1},
                "selection": "next",
                "count": 1,
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
        "How long until the next shuttle?",
        {
            "kind": "query",
            "answer_kind": "trips",
            "query": {
                "day": {"kind": "upcoming"},
                "selection": "next",
                "count": 1,
                "offset": 0,
                "route_mention": None,
                "origin_mention": None,
                "destination_mention": None,
                "time": None,
            },
            "show": "relative",
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
            "content": "What time does the shuttle arrive at the train station?",
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
                        "mentions": [],
                        "relation": "at",
                        "clock": "13:00",
                        "clock_mention": "1 PM",
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


def test_dated_availability_requires_user_authored_day_evidence() -> None:
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "Is there a shuttle at 1 PM?"}
    ]
    arguments = {
        "day": {
            "day_kind": "relative",
            "days_from_today": 0,
            "weekday": None,
            "service_day": None,
            "calendar_date": None,
            "day_mention": "today",
        },
        "mentions": [],
        "relation": "at",
        "clock": "13:00",
        "clock_mention": "1 PM",
        "basis": "departure",
    }

    with pytest.raises(InvalidTransportationInterpretation, match="day"):
        validate_tool_arguments(
            DATED_AVAILABILITY_TOOL_NAME,
            json.dumps(arguments),
            messages,
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
            "content": "The Saturday schedule has two scheduled trips.",
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


def test_last_shuttle_has_a_distinct_bounded_operation() -> None:
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "What is the last shuttle tonight?"}
    ]
    response = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=LAST_TRIP_TOOL_NAME,
                arguments=json.dumps(
                    {
                        "day": {
                            "day_kind": "relative",
                            "days_from_today": 0,
                            "weekday": None,
                            "service_day": None,
                            "calendar_date": None,
                            "day_mention": "tonight",
                        },
                        "mentions": [],
                        "show": "departure",
                    }
                ),
            )
        ],
        output_text="",
        model="gpt-test",
    )

    _, interpretation, _ = interpret(messages, response)

    assert isinstance(interpretation.request, ShuttleQueryRequest)
    assert interpretation.request.query.selection == "last"
    assert interpretation.request.query.count == 1


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (NEXT_TRIP_TOOL_NAME, {**next_wire_arguments(1), "trip_id": "invented-trip"}),
        (NEXT_TRIPS_TOOL_NAME, {**next_wire_arguments(3), "count_mention": None}),
        (
            AVAILABILITY_TOOL_NAME,
            {
                "day": cast(dict[str, object], next_wire_arguments(1)["day"]),
                "mentions": [],
                "relation": "around",
                "clock": "17:00Z",
                "clock_mention": "5 PM",
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
        validate_tool_arguments(NEXT_TRIP_TOOL_NAME, json.dumps(arguments), messages)


def test_model_cannot_invent_a_quantity_for_a_single_trip_question() -> None:
    arguments = next_wire_arguments(3)
    arguments["count_mention"] = "tomorrow"
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "When is the shuttle tomorrow?"}
    ]

    with pytest.raises(InvalidTransportationInterpretation, match="count"):
        validate_tool_arguments(NEXT_TRIPS_TOOL_NAME, json.dumps(arguments), messages)


def test_model_cannot_invent_midnight_for_an_arrival_question_without_a_clock() -> None:
    arguments = {
        "mentions": [{"role": "destination", "text": "the train station"}],
        "relation": "at",
        "clock": "00:00",
        "clock_mention": "time",
        "basis": "arrival",
    }
    messages: list[ConversationMessage] = [
        {
            "role": "user",
            "content": "What time does the shuttle arrive at the train station?",
        }
    ]

    with pytest.raises(InvalidTransportationInterpretation, match="clock"):
        validate_tool_arguments(AVAILABILITY_TOOL_NAME, json.dumps(arguments), messages)


def test_model_evidence_must_be_verbatim_user_text() -> None:
    arguments = next_wire_arguments(3)
    arguments["count_mention"] = "three"
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "When is the next shuttle?"}
    ]

    with pytest.raises(InvalidTransportationInterpretation, match="user text"):
        validate_tool_arguments(NEXT_TRIPS_TOOL_NAME, json.dumps(arguments), messages)


def test_invalid_semantic_model_output_becomes_safe_clarification() -> None:
    response = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=AVAILABILITY_TOOL_NAME,
                arguments=json.dumps(
                    {
                        "mentions": [],
                        "relation": "at",
                        "clock": "00:00",
                        "clock_mention": None,
                        "basis": "arrival",
                    }
                ),
            )
        ],
        output_text="",
        model="gpt-test",
    )
    messages: list[ConversationMessage] = [
        {"role": "user", "content": "When does the shuttle arrive?"}
    ]

    answer, interpretation, _ = interpret(messages, response)

    assert answer == INTERPRETATION_FAILURE_ANSWER
    assert interpretation.request == ShuttleClarificationRequest(
        kind="clarification", reason="interpretation_failure"
    )


def test_invalid_arrival_interpretation_retries_once_without_invented_clock() -> None:
    invalid = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=AVAILABILITY_TOOL_NAME,
                arguments=json.dumps(
                    {
                        "mentions": [{"role": "destination", "text": "train station"}],
                        "relation": "at",
                        "clock": "00:00",
                        "clock_mention": "time",
                        "basis": "arrival",
                    }
                ),
            )
        ],
        output_text="",
        model="gpt-test",
    )
    valid = Mock(
        output=[
            SimpleNamespace(
                type="function_call",
                name=NEXT_TRIP_TOOL_NAME,
                arguments=json.dumps(
                    {
                        "day": day_to_wire({"kind": "upcoming"}),
                        "mentions": [{"role": "destination", "text": "train station"}],
                        "offset": None,
                        "offset_mention": None,
                        "show": "arrival",
                    }
                ),
            )
        ],
        output_text="",
        model="gpt-test",
    )
    messages: list[ConversationMessage] = [
        {
            "role": "user",
            "content": "What time does the shuttle arrive at the train station?",
        }
    ]

    with patch("rockygpt_brain.transportation_interpretation.OpenAI") as client:
        client.return_value.responses.create.side_effect = [invalid, valid]
        answer, interpretation = interpret_transportation(messages, "gpt-test")

    assert answer == ""
    assert isinstance(interpretation.request, ShuttleQueryRequest)
    assert interpretation.request.answer_kind == "trips"
    assert interpretation.request.query.time is None
    assert interpretation.request.show == "arrival"
    assert client.return_value.responses.create.call_count == 2
    assert (
        client.return_value.responses.create.call_args_list[1].kwargs["instructions"]
        == INTERPRETATION_INSTRUCTIONS + RETRY_INSTRUCTIONS
    )
    retry_tool_names = {
        tool["name"]
        for tool in client.return_value.responses.create.call_args_list[1].kwargs["tools"]
    }
    assert AVAILABILITY_TOOL_NAME not in retry_tool_names
    assert DATED_AVAILABILITY_TOOL_NAME not in retry_tool_names


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
        DATED_AVAILABILITY_TOOL_NAME,
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
