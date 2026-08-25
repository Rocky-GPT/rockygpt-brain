"""A plan is only run when the registry allows every field it names."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from openai.lib._pydantic import to_strict_json_schema

from rockygpt_brain.core.capabilities import CAPABILITIES
from rockygpt_brain.core.plan import TIME_WORDS, Filter, Lane, Operation, Plan
from rockygpt_brain.core.planner import _PLAN
from rockygpt_brain.core.validate import Rejected, check, resolve

TZ = ZoneInfo("America/New_York")
NOW = datetime(2031, 3, 6, 18, 30, tzinfo=UTC).astimezone(TZ)


def code(capability: str, filters: dict[str, str], **operation: Any) -> Plan:
    """A CODE plan. Defaults to a stated operation, which every CODE plan needs."""
    return Plan(
        lane=Lane.CODE,
        resolved="a resolved question",
        capability=capability,
        filters=[Filter(field=k, value=v) for k, v in filters.items()],
        operation=Operation(**(operation or {"limit": 1})),
    )


# What a plan is allowed to say


def test_a_capability_keeps_the_fields_it_lists() -> None:
    plan = code(
        "shuttle", {"date": "today"}, order_by="departureTime", direction="descending", limit=1
    )
    checked = check(plan, NOW)
    assert isinstance(checked, Plan)
    assert checked.operation.order_by == "departureTime"
    assert checked.operation.limit == 1


def test_an_unknown_capability_is_rejected() -> None:
    assert isinstance(check(code("weather", {}), NOW), Rejected)


def test_a_filter_the_capability_does_not_offer_is_rejected() -> None:
    rejected = check(code("shuttle", {"venue": "Birch"}), NOW)
    assert isinstance(rejected, Rejected)
    assert "venue" in rejected.reason


def test_sorting_by_a_field_the_capability_does_not_have_is_rejected() -> None:
    assert isinstance(check(code("dining", {}, order_by="departureTime"), NOW), Rejected)


def test_comparing_a_field_the_capability_does_not_have_is_rejected() -> None:
    assert isinstance(check(code("dining", {}, compare=["departureTime"]), NOW), Rejected)


def test_a_plan_must_say_what_it_resolved_to() -> None:
    """Without it, nothing forces the reference to be worked out before the lane."""
    plan = Plan(lane=Lane.CODE, capability="shuttle", operation=Operation(limit=1))
    rejected = check(plan, NOW)
    assert isinstance(rejected, Rejected)
    assert "resolved" in rejected.reason


def test_a_capability_without_an_operation_is_rejected() -> None:
    """Half a plan: what to look in, and nothing about what to do with it."""
    plan = Plan(lane=Lane.CODE, resolved="q", capability="shuttle", operation=Operation())
    rejected = check(plan, NOW)
    assert isinstance(rejected, Rejected)
    assert "operation" in rejected.reason


def test_a_direction_alone_is_not_an_operation() -> None:
    """It has a default, so it is set on every plan whether meant or not."""
    plan = Plan(
        lane=Lane.CODE,
        resolved="q",
        capability="shuttle",
        operation=Operation(direction="descending"),
    )
    assert isinstance(check(plan, NOW), Rejected)


def test_counting_needs_no_field_and_so_is_always_allowed() -> None:
    assert isinstance(check(code("shuttle", {"date": "today"}, count=True), NOW), Plan)


# Time is resolved in Python, never by the model


def test_a_time_word_becomes_a_date() -> None:
    checked = check(code("shuttle", {"date": "tomorrow"}), NOW)
    assert isinstance(checked, Plan)
    assert checked.filter_values["date"] == "2031-03-07"


def test_now_becomes_an_instant_not_a_date() -> None:
    checked = check(code("shuttle", {"departingAfter": "now"}), NOW)
    assert isinstance(checked, Plan)
    assert checked.filter_values["departingAfter"] == NOW.isoformat()


def test_every_time_word_resolves_to_something_else() -> None:
    for word in TIME_WORDS:
        assert resolve(word, NOW) != word, word


def test_a_value_that_is_not_a_time_word_is_left_alone() -> None:
    checked = check(code("shuttle", {"destination": "Garden State Plaza"}), NOW)
    assert isinstance(checked, Plan)
    assert checked.filter_values["destination"] == "Garden State Plaza"


# The other lanes


def test_a_rag_plan_needs_a_topic() -> None:
    assert isinstance(check(Plan(lane=Lane.RAG, resolved="q"), NOW), Rejected)
    assert isinstance(
        check(Plan(lane=Lane.RAG, resolved="q", topic="overnight guest policy"), NOW), Plan
    )


def test_a_memory_plan_needs_a_query() -> None:
    assert isinstance(check(Plan(lane=Lane.MEMORY, resolved="q"), NOW), Rejected)
    assert isinstance(check(Plan(lane=Lane.MEMORY, resolved="q", query="the shuttle"), NOW), Plan)


def test_general_and_safety_need_nothing() -> None:
    assert isinstance(check(Plan(lane=Lane.GENERAL, resolved="q"), NOW), Plan)
    assert isinstance(check(Plan(lane=Lane.SAFETY, resolved="q"), NOW), Plan)


def test_a_stray_field_from_another_lane_is_dropped_not_rejected() -> None:
    checked = check(Plan(lane=Lane.RAG, resolved="q", topic="parking", capability="shuttle"), NOW)
    assert isinstance(checked, Plan)
    assert checked.capability is None


# The shape a human reads


def test_the_summary_reads_as_the_plan_was_written() -> None:
    checked = check(
        code(
            "shuttle",
            {"destination": "Garden State Plaza", "date": "today"},
            order_by="departureTime",
            limit=1,
        ),
        NOW,
    )
    assert isinstance(checked, Plan)
    assert checked.summary() == {
        "lane": "CODE",
        "resolved": "a resolved question",
        "capability": "shuttle",
        "filters": {"destination": "Garden State Plaza", "date": "2031-03-06"},
        "operation": {"orderBy": "departureTime", "direction": "ascending", "limit": 1},
    }


def test_an_unused_half_of_the_plan_is_not_in_the_summary() -> None:
    checked = check(Plan(lane=Lane.GENERAL, resolved="q"), NOW)
    assert checked.summary() == {  # type: ignore[union-attr]
        "lane": "GENERAL",
        "resolved": "q",
        "freshness": "stable",
    }


# The vocabulary stays a vocabulary


def test_the_plan_is_a_shape_the_model_can_be_held_to() -> None:
    """Every object closed. An open one is what a filter map would produce."""
    schema = to_strict_json_schema(Plan)

    def closed(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node.get("title")
            for value in node.values():
                closed(value)
        elif isinstance(node, list):
            for value in node:
                closed(value)

    closed(schema)
    assert schema["$defs"]["Lane"]["enum"] == [lane.value for lane in Lane]


def test_the_instruction_carries_no_example_question() -> None:
    """A worked example in the prompt is the first step back to an intent list."""
    assert "?" not in _PLAN


def test_no_capability_is_named_after_a_question() -> None:
    for name in CAPABILITIES:
        assert "_" not in name, f"{name} reads as an intent, not a capability"
