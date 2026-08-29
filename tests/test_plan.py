from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openai.lib._pydantic import to_strict_json_schema

import rockygpt_brain
import rockygpt_brain.brain
from rockygpt_brain.brain.execute.schema import COMPACT_UP_TO, present
from rockygpt_brain.brain.plan.run import PLAN
from rockygpt_brain.brain.plan.schema import (
    MOST_ROWS,
    NOT_ASKED,
    TIME_WORDS,
    Filter,
    Operation,
    Plan,
)
from rockygpt_brain.brain.plan.validate import Rejected, anchor, check, resolve
from rockygpt_brain.brain.understand.run import UNDERSTAND
from rockygpt_brain.brain.write.run import ANSWER
from rockygpt_brain.capabilities.registry import CAPABILITIES, catalogue
from rockygpt_brain.lanes.code.run import apply
from rockygpt_brain.safety.schema import Concern

SOURCE = Path(rockygpt_brain.__file__).parent
TZ = ZoneInfo("America/New_York")
NOW = datetime(2031, 3, 6, 18, 30, tzinfo=UTC).astimezone(TZ)


def code(capability: str, filters: dict[str, str], **operation: Any) -> Plan:
    return Plan(
        a_capability_answers_it=True,
        capability=capability,
        filters=[Filter(field=k, value=v) for k, v in filters.items()],
        operation=Operation(**(operation or {"limit": 1})),
    )


def test_a_capability_keeps_the_fields_it_lists() -> None:
    plan = code(
        "shuttle", {"date": "today"}, order_by="departureTime", direction="descending", limit=5
    )
    checked = check(plan, NOW)
    assert isinstance(checked, Plan)
    assert checked.operation.order_by == "departureTime"
    assert checked.operation.limit == 5


def test_a_count_the_question_named_is_kept() -> None:
    checked = check(code("shuttle", {}, order_by="departureTime", limit=3), NOW)
    assert isinstance(checked, Plan)
    assert checked.operation.limit == 3
    assert checked.operation.select is None


def test_a_limit_of_one_is_not_a_way_to_select_one_row() -> None:
    """The planner reaches for `limit: 1` whenever a question reads as singular.

    A count of one and the first row of an order are the same rows and
    different questions, so the one value where they collide is dropped rather
    than honoured: rows the question never divided stay undivided, and a plan
    that meant to take one ordered row has `select` to say so.
    """
    checked = check(code("calendar", {"family": "registration"}, order_by="startsAt", limit=1), NOW)
    assert isinstance(checked, Plan)
    assert checked.operation.limit is None
    assert checked.operation.select is None
    assert checked.operation.order_by == "startsAt"


def test_a_selection_survives_because_it_says_what_it_means() -> None:
    checked = check(
        code("shuttle", {}, order_by="departureTime", direction="ascending", select="first"), NOW
    )
    assert isinstance(checked, Plan)
    assert checked.operation.select == "first"
    assert checked.operation.limit is None


def test_the_last_of_something_is_the_first_of_the_reverse_order() -> None:
    checked = check(
        code("shuttle", {}, order_by="departureTime", direction="descending", select="first"), NOW
    )
    assert isinstance(checked, Plan)
    assert checked.operation.direction == "descending"
    assert checked.operation.select == "first"


def test_selecting_one_row_out_of_no_order_is_rejected() -> None:
    rejected = check(code("shuttle", {}, select="first"), NOW)
    assert isinstance(rejected, Rejected)
    assert "no order" in rejected.reason


def test_a_dropped_limit_still_leaves_an_operation_to_run() -> None:
    """Dropping the count must not turn a stated operation into no operation."""
    checked = check(code("calendar", {"family": "registration"}, order_by="date", limit=1), NOW)
    assert isinstance(checked, Plan)
    assert checked.operation.stated


def test_a_plan_that_asks_for_nothing_to_be_done_is_run_not_refused() -> None:
    """Asking what there is names no operation, and that is a plan, not a gap.

    `direction` has a default, so an operation carrying only that is the shape
    the planner produces when the question asked for nothing to be done to the
    rows. It was refused outright until 2026-08-29, which made roughly one
    dining question in three return a 503 instead of the menu.
    """
    checked = check(code("dining", {"date": "today"}, direction="ascending"), NOW)
    assert isinstance(checked, Plan)
    assert not checked.operation.stated
    assert checked.capability == "dining"
    assert [(f.field, f.value) for f in checked.filters] == [("date", NOW.date().isoformat())]


def test_nothing_asked_for_leaves_every_row_in_its_original_order() -> None:
    """An operation that asks for nothing must not quietly sort or truncate."""
    rows = [{"name": n} for n in ("Waffles", "Apples", "Bagels")]
    execution = apply(rows, Operation(), "dining")
    assert execution.ordering is None
    assert [row["name"] for row in execution.results] == ["Waffles", "Apples", "Bagels"]


def test_an_unknown_capability_is_rejected() -> None:
    assert isinstance(check(code("weather", {}), NOW), Rejected)


def test_a_filter_the_capability_does_not_offer_is_rejected() -> None:
    rejected = check(code("shuttle", {"venue": "Birch"}), NOW)
    assert isinstance(rejected, Rejected)
    assert "venue" in rejected.reason


def test_sorting_by_a_field_the_capability_does_not_have_is_rejected() -> None:
    rejected = check(code("shuttle", {}, order_by="opensAt"), NOW)
    assert isinstance(rejected, Rejected)
    assert "opensAt" in rejected.reason


def test_comparing_a_field_the_capability_does_not_have_is_rejected() -> None:
    rejected = check(code("shuttle", {}, compare=["opensAt"]), NOW)
    assert isinstance(rejected, Rejected)
    assert "opensAt" in rejected.reason


def test_a_capability_with_no_code_behind_it_is_rejected_before_it_can_fail() -> None:
    rejected = check(code("parking", {}, order_by="opensAt"), NOW)
    assert isinstance(rejected, Rejected)
    assert "parking" in rejected.reason


def test_dining_accepts_only_its_published_filters_and_fields() -> None:
    checked = check(code("dining", {"meal": "LUNCH", "dietary": "vegan"}, order_by="calories"), NOW)
    assert isinstance(checked, Plan)
    assert checked.filter_values == {"meal": "lunch", "dietary": "vegan"}
    # A narrowing dining cannot do, on an unpublished filter:
    assert isinstance(check(code("dining", {"route": "main"}, order_by="name"), NOW), Rejected)


def test_the_catalogue_tells_the_planner_each_filters_value_type() -> None:
    dining = next(entry for entry in catalogue() if entry["capability"] == "dining")
    filters = {entry["field"]: entry for entry in dining["filters"]}
    assert filters["meal"] == {
        "field": "meal",
        "type": "enum",
        "values": ["breakfast", "brunch", "dinner", "late_night", "lunch"],
        "multiple": True,
    }
    assert filters["station"] == {
        "field": "station",
        "type": "entity",
        "entity": "dining_station",
    }
    assert filters["date"] == {"field": "date", "type": "date"}


def test_an_enum_refuses_a_time_value_instead_of_repairing_it() -> None:
    rejected = check(code("dining", {"meal": "today"}, order_by="name"), NOW)
    assert isinstance(rejected, Rejected)
    assert rejected.reason == (
        "dining.meal expects one of breakfast, brunch, dinner, late_night, lunch, received 'today'"
    )


def test_a_question_naming_two_meals_keeps_both() -> None:
    checked = check(code("dining", {"meal": "breakfast, dinner"}, order_by="name"), NOW)
    assert isinstance(checked, Plan)
    assert checked.filter_values == {"meal": "breakfast,dinner"}


def test_naming_the_same_meal_twice_asks_for_it_once() -> None:
    checked = check(code("dining", {"meal": "dinner, DINNER"}, order_by="name"), NOW)
    assert isinstance(checked, Plan)
    assert checked.filter_values == {"meal": "dinner"}


def test_one_unknown_meal_refuses_the_whole_filter() -> None:
    rejected = check(code("dining", {"meal": "breakfast, elevenses"}, order_by="name"), NOW)
    assert isinstance(rejected, Rejected)
    assert "dining.meal expects one of" in rejected.reason


def test_a_filter_that_takes_one_value_still_refuses_two() -> None:
    rejected = check(code("dining", {"dietary": "vegan, vegetarian"}, order_by="name"), NOW)
    assert isinstance(rejected, Rejected)
    assert "dining.dietary expects one of" in rejected.reason


def test_an_enum_also_refuses_a_date_shaped_value() -> None:
    rejected = check(code("dining", {"meal": "2026-08-27"}, order_by="name"), NOW)
    assert isinstance(rejected, Rejected)
    assert "dining.meal expects one of" in rejected.reason


def test_a_date_word_is_only_resolved_on_a_date_filter() -> None:
    checked = check(code("dining", {"meal": "lunch", "date": "today"}, order_by="name"), NOW)
    assert isinstance(checked, Plan)
    assert checked.filter_values == {"meal": "lunch", "date": "2031-03-06"}


def test_asking_for_another_day_in_an_enum_is_also_rejected() -> None:
    rejected = check(code("dining", {"meal": "tomorrow"}, order_by="name"), NOW)
    assert isinstance(rejected, Rejected)
    assert "dining.meal expects one of" in rejected.reason


def test_events_resolve_date_and_time_filters_before_execution() -> None:
    checked = check(
        code("events", {"date": "tomorrow", "startsAfter": "now"}, order_by="startTime"), NOW
    )
    assert isinstance(checked, Plan)
    assert checked.filter_values == {
        "date": "2031-03-07",
        "startsAfter": NOW.isoformat(),
    }


def test_hours_accept_named_venues_dates_and_open_instants() -> None:
    checked = check(
        code(
            "hours",
            {"kind": "campus", "name": "Library", "date": "tomorrow", "openAt": "now"},
            limit=1,
        ),
        NOW,
    )
    assert isinstance(checked, Plan)
    assert checked.filter_values["date"] == "2031-03-07"
    assert checked.filter_values["openAt"] == NOW.isoformat()


def test_courses_publish_catalog_filters_and_fields() -> None:
    checked = check(
        code(
            "courses",
            {"subject": "COMP", "attribute": "Scientific Reasoning"},
            order_by="code",
        ),
        NOW,
    )
    assert isinstance(checked, Plan)
    assert checked.operation.order_by == "code"
    assert isinstance(check(code("courses", {"instructor": "Ada"}), NOW), Rejected)


def test_a_last_day_phrase_is_not_treated_as_descending_chronology() -> None:
    assert "names a deadline" in PLAN
    assert "not request descending chronology" in PLAN


def test_a_capability_without_an_operation_is_run() -> None:
    """Naming no operation asks for the rows themselves, which is a plan.

    This asserted a rejection until 2026-08-29, on the reasoning that a
    capability without an operation is half a plan. It is not: a question can
    ask what there is and nothing more, and then the filtered rows are the
    whole answer. Refusing them returned a 503 to roughly one dining question
    in three, where the rows were sitting there ready to be written up.
    """
    plan = Plan(a_capability_answers_it=True, capability="shuttle", operation=Operation())
    checked = check(plan, NOW)
    assert isinstance(checked, Plan)
    assert checked.capability == "shuttle"


def test_a_direction_alone_is_not_an_operation() -> None:
    """`direction` has a default, so it is set whether or not one was meant.

    Still true, and still worth holding — `selective` relies on it to tell a
    dropped limit from an operation that never asked for anything. It no longer
    decides whether the plan runs.
    """
    assert not Operation(direction="descending").stated
    plan = Plan(
        a_capability_answers_it=True,
        capability="shuttle",
        operation=Operation(direction="descending"),
    )
    assert isinstance(check(plan, NOW), Plan)


def test_counting_needs_no_field_and_so_is_always_allowed() -> None:
    assert isinstance(check(code("shuttle", {"date": "today"}, count=True), NOW), Plan)


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


def test_a_rag_plan_needs_a_topic() -> None:
    assert isinstance(check(Plan(specific_to_ramapo=True), NOW), Rejected)
    assert isinstance(
        check(Plan(specific_to_ramapo=True, topic="overnight guest policy"), NOW), Plan
    )


def test_general_needs_nothing() -> None:
    assert isinstance(check(Plan(), NOW), Plan)


def test_safety_survives_a_plan_that_would_otherwise_be_rejected() -> None:
    assert isinstance(check(Plan(a_capability_answers_it=True), NOW), Rejected)
    checked = check(Plan(safety=[Concern.EMERGENCY], a_capability_answers_it=True), NOW)
    assert isinstance(checked, Plan)
    assert checked.safety == [Concern.EMERGENCY]


def test_every_concern_is_carried_through_the_check_not_just_the_first() -> None:
    both = [Concern.PRIVACY, Concern.SECRET]
    for plan in (
        Plan(safety=both),
        Plan(safety=both, specific_to_ramapo=True, topic="anything"),
        Plan(safety=both, a_capability_answers_it=True, capability="shuttle"),
    ):
        checked = check(plan, NOW)
        assert isinstance(checked, Plan)
        assert checked.safety == both, f"{plan.lane} dropped a concern"


def test_the_concerns_lead_the_logged_plan() -> None:
    summary = Plan(safety=[Concern.PRIVACY, Concern.SECRET], a_capability_answers_it=True).summary()
    assert list(summary)[0] == "safety"
    assert summary["safety"] == ["privacy", "secret"]


def test_a_stray_field_from_another_lane_is_dropped_not_rejected() -> None:
    checked = check(Plan(specific_to_ramapo=True, topic="parking", capability="shuttle"), NOW)
    assert isinstance(checked, Plan)
    assert checked.capability is None


def test_the_summary_reads_as_the_plan_was_written() -> None:
    checked = check(
        code(
            "shuttle",
            {"destination": "Garden State Plaza", "date": "today"},
            order_by="departureTime",
            select="first",
        ),
        NOW,
    )
    assert isinstance(checked, Plan)
    assert checked.summary() == {
        "routing": {"CODE?": "Yes", "RAMAPO?": "—", "ROUTE": "CODE"},
        "capability": "shuttle",
        "filters": {"destination": "Garden State Plaza", "date": "2031-03-06"},
        "operation": {
            "orderBy": "departureTime",
            "direction": "ascending",
            "select": "first",
        },
    }


def test_an_unused_half_of_the_plan_is_not_in_the_summary() -> None:
    checked = check(Plan(), NOW)
    assert checked.summary() == {  # type: ignore[union-attr]
        "routing": {"CODE?": "No", "RAMAPO?": "No", "ROUTE": "GENERAL"},
        "freshness": "stable",
    }


def test_the_summary_shows_why_the_lane_was_chosen() -> None:
    checked = check(Plan(specific_to_ramapo=True, topic="parking"), NOW)
    assert isinstance(checked, Plan)
    routing = checked.summary()["routing"]
    assert list(routing) == ["CODE?", "RAMAPO?", "ROUTE"], "the questions, then the answer"
    assert routing == {"CODE?": "No", "RAMAPO?": "Yes", "ROUTE": "RAG"}


def test_the_plan_is_a_shape_the_model_can_be_held_to() -> None:
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


def test_the_planner_is_not_asked_where_the_answer_comes_from() -> None:
    assert "lane" not in to_strict_json_schema(Plan)["properties"]
    assert "Lane" not in to_strict_json_schema(Plan).get("$defs", {})


def test_the_instruction_names_no_campus_thing() -> None:
    for thing in ("shuttle", "menu", "registrar", "dining", "parking", "garden state"):
        assert thing not in PLAN.lower(), f"the instruction names {thing!r}"


def test_every_stage_loads_its_instruction_from_disk() -> None:
    for instruction in (UNDERSTAND, PLAN, ANSWER):
        assert instruction and not instruction.startswith("#")


def test_every_model_instruction_is_a_prompt_md() -> None:
    senders = [
        path for path in SOURCE.rglob("*.py") if "instructions=" in path.read_text(encoding="utf-8")
    ]
    assert senders, "no model call found — this test has stopped testing anything"
    for path in senders:
        text = path.read_text(encoding="utf-8")
        assert "beside(__file__)" in text or "instructions: str" in text, (
            f"{path.name} sends an instruction that is not loaded from a prompt.md"
        )


def test_a_prompt_file_is_the_whole_instruction() -> None:
    for stage, instruction in (
        ("brain/understand", UNDERSTAND),
        ("brain/plan", PLAN),
        ("brain/write", ANSWER),
    ):
        whole = (SOURCE / stage / "prompt.md").read_text(encoding="utf-8")
        assert whole.strip() == instruction


def test_no_capability_is_named_after_a_question() -> None:
    for name in CAPABILITIES:
        assert "_" not in name, f"{name} reads as an intent, not a capability"


def test_the_server_clock_dates_every_current_query() -> None:
    assert anchor("population of France", NOW) == f"population of France as of {NOW:%Y-%m-%d}"


def test_the_date_is_added_with_no_condition_on_the_planners_wording() -> None:
    for query in ("who won the 2018 world cup", "price of milk", "current price of gold"):
        assert anchor(query, NOW) == f"{query} as of {NOW:%Y-%m-%d}"


def test_a_date_the_planner_wrote_anyway_is_replaced_not_doubled() -> None:
    stamp = f"as of {NOW:%Y-%m-%d}"
    assert anchor(f"population of France {stamp}", NOW) == f"population of France {stamp}"
    assert anchor("population of France as of the most recent data", NOW) == (
        f"population of France {stamp}"
    )


def test_words_of_meaning_are_not_mistaken_for_a_date() -> None:
    assert anchor("current president of France", NOW).startswith("current president of France")


def test_a_current_plan_carries_both_the_meaning_and_the_dated_query() -> None:
    checked = check(Plan(freshness="current", query="population of France"), NOW)
    assert isinstance(checked, Plan)
    assert checked.query == "population of France", "the planner's meaning is kept as written"
    assert checked.effective_query == f"population of France as of {NOW:%Y-%m-%d}"
    assert checked.summary()["effectiveQuery"] == checked.effective_query


def test_the_second_question_is_blank_when_the_cascade_never_reached_it() -> None:
    for stated in (True, False):
        checked = check(
            Plan(
                a_capability_answers_it=True,
                specific_to_ramapo=stated,
                capability="shuttle",
                operation=Operation(limit=1),
            ),
            NOW,
        )
        assert isinstance(checked, Plan)
        assert checked.summary()["routing"]["RAMAPO?"] == NOT_ASKED


def test_the_second_question_is_answered_when_the_cascade_reaches_it() -> None:
    checked = check(Plan(specific_to_ramapo=True, topic="parking"), NOW)
    assert isinstance(checked, Plan)
    assert checked.summary()["routing"] == {"CODE?": "No", "RAMAPO?": "Yes", "ROUTE": "RAG"}


def test_a_plan_can_ask_for_as_many_rows_as_can_be_handed_over() -> None:
    schema = to_strict_json_schema(Plan)["$defs"]["Operation"]["properties"]["limit"]
    allowed = next(one for one in schema["anyOf"] if one.get("type") == "integer")
    assert allowed["maximum"] == MOST_ROWS
    assert MOST_ROWS >= 100, "a question can plausibly ask for a hundred of something"


def test_the_largest_result_a_plan_may_ask_for_still_fits_one_message() -> None:
    assert present(MOST_ROWS).page_size <= COMPACT_UP_TO
