"""Calculation units, provenance, endpoint roles and real elapsed-time boundaries."""

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from rockygpt_brain.calculations import CalculationQuery, TimeOperand, calculate
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.data import SearchQuery
from rockygpt_brain.engine import run_turn
from rockygpt_brain.schedules import departure_summary, schedule_references
from test_engine import answer, review, tools
from test_formats import hours, menu, messages
from test_schedules import NOW, QUERY, output, record


def times(operation: str, *operands: TimeOperand) -> CalculationQuery:
    return CalculationQuery(operation=operation, times=list(operands))  # type: ignore[arg-type]


def evidence_time(value: str, record_id: str, field: str, point: str | None = None) -> TimeOperand:
    return TimeOperand.model_validate(
        {
            "source": "evidence",
            "value": value,
            "evidence_id": record_id,
            "field": field,
            "point": point,
        }
    )


def test_user_measurements_preserve_units_and_refuse_invented_or_mixed_units() -> None:
    query = CalculationQuery.model_validate(
        {
            "operation": "sum",
            "operands": [
                {"source": "user", "value": "20", "unit": "minutes"},
                {"source": "user", "value": "15", "unit": "minutes"},
            ],
        }
    )
    result = calculate(
        query, {}, messages("Allow 20 minutes eating and 15 minutes walking"), NOW.date()
    )
    assert result["result"] == "35" and result["unit"] == "minutes"
    with pytest.raises(ValueError, match="unit"):
        calculate(query, {}, messages("Allow 20 dollars and 15 minutes"), NOW.date())
    query.operands[1].unit = "hours"
    with pytest.raises(ValueError, match="units"):
        calculate(query, {}, messages("Compare 20 minutes and 15 hours"), NOW.date())


def test_numeric_sort_preserves_decimal_order_and_source_indices() -> None:
    query = CalculationQuery.model_validate(
        {
            "operation": "sort",
            "operands": [
                {"source": "user", "value": value} for value in ["10", "-2", "2.1", "2.1"]
            ],
        }
    )
    result = calculate(query, {}, messages("Sort 10, -2, 2.1, 2.1"), NOW.date())
    assert result["result"] == ["-2", "2.1", "2.1", "10"]
    assert result["ordered_operand_indices"] == [1, 2, 3, 0]


def test_count_is_explicitly_scoped_to_supplied_records_and_rejects_duplicates() -> None:
    rows = {"one": menu(), "two": menu("Tofu")}
    query = CalculationQuery(operation="count", evidence_ids=list(rows))
    result = calculate(query, rows, messages("How many of these records?"), NOW.date())
    assert result["result"] == "2" and result["unit"] == "records"
    assert "not a count of all" in result["limitations"][0]
    with pytest.raises(ValidationError, match="Duplicate"):
        CalculationQuery(operation="count", evidence_ids=["one", "one"])
    with pytest.raises(ValueError):
        calculate(query, {"one": rows["one"]}, messages("How many?"), NOW.date())
    rows["two"]["freshness"] = "stale"
    with pytest.raises(ValueError):
        calculate(query, rows, messages("How many?"), NOW.date())


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("2026-03-08T01:30:00-05:00", "2026-03-08T03:30:00-04:00", "60.0"),
        ("2026-11-01T01:30:00-04:00", "2026-11-01T01:30:00-05:00", "60.0"),
        ("2026-09-16T23:40:00-04:00", "2026-09-17T00:10:00-04:00", "30.0"),
        ("2026-09-16T18:00:00-04:00", "2026-09-16T17:30:00-04:00", "-30.0"),
    ],
)
def test_explicit_user_times_use_signed_elapsed_minutes(
    first: str, second: str, expected: str
) -> None:
    query = times(
        "duration",
        TimeOperand(source="user", value=first),
        TimeOperand(source="user", value=second),
    )
    result = calculate(query, {}, messages(f"Between {first} and {second}?"), NOW.date())
    assert result["result"] == expected and result["unit"] == "elapsed_minutes"
    assert result["operands"][0]["value"] == first


@pytest.mark.parametrize(
    ("second", "expected"),
    [
        ("2026-09-16T19:00:00-04:00", "before"),
        ("2026-09-16T17:00:00-04:00", "after"),
        ("2026-09-16T22:00:00+00:00", "same_instant"),
    ],
)
def test_time_comparison_returns_order_not_a_plan(second: str, expected: str) -> None:
    first = "2026-09-16T18:00:00-04:00"
    query = times(
        "compare_times",
        TimeOperand(source="user", value=first),
        TimeOperand(source="user", value=second),
    )
    result = calculate(query, {}, messages(f"Compare {first} to {second}"), NOW.date())
    assert result["result"] == expected
    assert "eating/walking" in result["limitations"][0]


def test_unknown_naive_or_assistant_invented_times_are_not_operands() -> None:
    with pytest.raises(ValidationError, match="offset"):
        TimeOperand(source="user", value="2026-09-16T18:00:00")
    value = "2026-09-16T18:00:00-04:00"
    operand = TimeOperand(source="user", value=value)
    query = times("duration", operand, operand)
    history = (
        messages("Guess a time")
        + [ChatMessage(role="assistant", content=value)]
        + messages("Use that")
    )
    with pytest.raises(ValueError, match="absent"):
        calculate(query, {}, history, NOW.date())
    with pytest.raises(ValueError, match="absent"):
        calculate(query, {}, messages(f"Use {value}1"), NOW.date())


def test_overnight_hours_keep_service_date_and_next_day_closing() -> None:
    row = hours("05:00 PM - 01:00 AM")
    query = times(
        "duration",
        evidence_time("2026-09-16T17:00:00-04:00", row["id"], "opening"),
        evidence_time("2026-09-17T01:00:00-04:00", row["id"], "closing"),
    )
    result = calculate(query, {row["id"]: row}, messages("How long is it open?"), NOW.date())
    assert result["result"] == "480.0"
    row["fields"]["schedule"] = "05:00 PM - 05:00 PM"
    with pytest.raises(ValueError, match="24-hour"):
        calculate(query, {row["id"]: row}, messages("How long?"), NOW.date())


def test_event_time_requires_published_exact_timestamp() -> None:
    value = "2026-09-16T18:00:00-04:00"
    row = {
        **menu(),
        "collection": "events",
        "fields": {"starts_at": value},
        "coverage": {"fields": {"starts_at": "published"}},
    }
    operand = evidence_time(value, row["id"], "starts_at")
    query = times("compare_times", operand, operand)
    assert (
        calculate(query, {row["id"]: row}, messages("Compare"), NOW.date())["result"]
        == "same_instant"
    )
    row["coverage"]["fields"]["starts_at"] = "unknown"
    with pytest.raises(ValueError, match="published"):
        calculate(query, {row["id"]: row}, messages("Compare"), NOW.date())


def test_shuttle_arrival_and_departure_cannot_be_interchanged() -> None:
    trip = record(1, "5:30 PM", "5:40 PM", "6:00 PM")
    references = schedule_references(departure_summary(output(trip), QUERY, NOW))
    departing = evidence_time(
        "2026-09-16T17:30:00-04:00", trip["id"], "scheduled_departure", "campus"
    )
    returning = evidence_time(
        "2026-09-16T18:00:00-04:00", trip["id"], "scheduled_arrival", "campus"
    )
    query = times("duration", departing, returning)
    result = calculate(
        query, {trip["id"]: trip}, messages("Trip duration?"), NOW.date(), references
    )
    assert result["result"] == "30.0"
    returning.field = "scheduled_departure"
    with pytest.raises(ValueError, match="does not match"):
        calculate(query, {trip["id"]: trip}, messages("Trip duration?"), NOW.date(), references)
    with pytest.raises(ValueError):
        calculate(query, {trip["id"]: trip}, messages("Trip duration?"), NOW.date())


def test_incomplete_timetable_produces_no_calculation_references() -> None:
    trip = record(1, "5:30 PM", "5:40 PM", "6:00 PM")
    summary = departure_summary({**output(trip), "truncated": True}, QUERY, NOW)
    assert schedule_references(summary) == {}


def test_engine_passes_verified_schedule_references_to_time_tool() -> None:
    venue = hours()
    trip = record(1, "9 PM", "9:10 PM", "9:30 PM")
    trip.update(title="Published shuttle", url="https://www.ramapo.edu/shuttle/")
    calculation = times(
        "duration",
        evidence_time("2026-09-16T20:00:00-04:00", venue["id"], "closing"),
        evidence_time("2026-09-16T21:00:00-04:00", trip["id"], "scheduled_departure", "campus"),
    )

    def call(name: str, call_id: str, args: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(
            type="function_call", name=name, call_id=call_id, arguments=json.dumps(args)
        )

    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(
            call(
                "search_campus",
                "hours",
                SearchQuery(collection="dining_hours", date_from=NOW.date()).model_dump(
                    mode="json"
                ),
            ),
            call("search_campus", "shuttle", QUERY.model_dump(mode="json")),
        ),
        tools(call("calculate", "gap", calculation.model_dump(mode="json"))),
        answer(
            "The scheduled departure is 60 minutes after the published closing time.",
            "campus_fact",
            [venue["id"], trip["id"]],
        ),
        review(),
    ]
    data.search.side_effect = [output(venue), output(trip)]
    result = run_turn(
        messages("How long after closing is the next departure?"),
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["status"] == "answered" and result["metrics"]["modelCalls"] == 4
    assert result["trace"][-1]["status"] == "ok"
    sent = client.create.call_args_list[2].kwargs["input"]
    calculation_output = next(x for x in sent if isinstance(x, dict) and x.get("call_id") == "gap")
    assert json.loads(calculation_output["output"])["result"] == "60.0"


def test_duplicate_schedule_records_cannot_inflate_counts() -> None:
    trip = record(1, "5:30 PM", "5:40 PM", "6:00 PM")
    assert departure_summary(output(trip, deepcopy(trip)), QUERY, NOW)["status"] == "unavailable"


def test_followup_operands_reference_specific_user_messages_only() -> None:
    history = [
        ChatMessage(role="user", content="Allow 20 minutes eating and 15 minutes walking."),
        ChatMessage(role="assistant", content="A made-up estimate is 99 minutes."),
        ChatMessage(role="user", content="Make the eating time 25 minutes. What is the total now?"),
    ]
    query = CalculationQuery.model_validate(
        {
            "operation": "sum",
            "operands": [
                {"source": "user", "value": "25", "unit": "minutes"},
                {"source": "user", "value": "15", "unit": "minutes", "user_message_index": 0},
            ],
        }
    )
    result = calculate(query, {}, history, NOW.date())
    assert result["result"] == "40"
    assert result["operands"][1]["user_message_index"] == 0
    query.operands[1].user_message_index = None
    with pytest.raises(ValueError, match="absent"):
        calculate(query, {}, history, NOW.date())
    query.operands[1].value = "99"
    for index in [1, 3, 79]:
        query.operands[1].user_message_index = index
        with pytest.raises(ValueError, match="user message"):
            calculate(query, {}, history, NOW.date())


def test_followup_times_keep_explicit_user_provenance_and_timezone() -> None:
    first = "2026-09-16T23:40:00-04:00"
    second = "2026-09-17T00:10:00-04:00"
    history = [
        ChatMessage(role="user", content=f"I start at {first}."),
        ChatMessage(role="assistant", content=f"Perhaps finish at {second}."),
        ChatMessage(role="user", content=f"Now compare that start to {second}."),
    ]
    query = times(
        "duration",
        TimeOperand(source="user", value=first, user_message_index=0),
        TimeOperand(source="user", value=second),
    )
    assert calculate(query, {}, history, NOW.date())["result"] == "30.0"
    query.times[1].user_message_index = 1
    with pytest.raises(ValueError, match="user message"):
        calculate(query, {}, history, NOW.date())
    with pytest.raises(ValidationError, match="cannot reference a user message"):
        TimeOperand(
            source="evidence",
            evidence_id="event:one",
            field="starts_at",
            value=first,
            user_message_index=0,
        )
