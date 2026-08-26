"""A plan is only run when the registry allows every field it names."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openai.lib._pydantic import to_strict_json_schema

import rockygpt_brain
import rockygpt_brain.brain
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
from rockygpt_brain.capabilities.registry import CAPABILITIES
from rockygpt_brain.lanes.code.run import GROUNDING_ROWS
from rockygpt_brain.safety.schema import Concern

SOURCE = Path(rockygpt_brain.__file__).parent
TZ = ZoneInfo("America/New_York")
NOW = datetime(2031, 3, 6, 18, 30, tzinfo=UTC).astimezone(TZ)


def code(capability: str, filters: dict[str, str], **operation: Any) -> Plan:
    """A CODE plan. Defaults to a stated operation, which every CODE plan needs."""
    return Plan(
        a_capability_answers_it=True,
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
    rejected = check(code("shuttle", {}, order_by="opensAt"), NOW)
    assert isinstance(rejected, Rejected)
    assert "opensAt" in rejected.reason


def test_comparing_a_field_the_capability_does_not_have_is_rejected() -> None:
    rejected = check(code("shuttle", {}, compare=["opensAt"]), NOW)
    assert isinstance(rejected, Rejected)
    assert "opensAt" in rejected.reason


def test_a_capability_with_no_code_behind_it_is_rejected_before_it_can_fail() -> None:
    """The registry lists only what can run, so an unknown name stops here.

    It used to be listed and unbuilt, which failed a whole stage later — after
    the question was understood and a plan was made, where nothing recovers.
    """
    rejected = check(code("parking", {}, order_by="opensAt"), NOW)
    assert isinstance(rejected, Rejected)
    assert "parking" in rejected.reason


def test_dining_accepts_only_its_published_filters_and_fields() -> None:
    checked = check(code("dining", {"meal": "LUNCH", "dietary": "vegan"}, order_by="calories"), NOW)
    assert isinstance(checked, Plan)
    assert checked.filter_values == {"meal": "LUNCH", "dietary": "vegan"}
    assert isinstance(check(code("dining", {"date": "today"}), NOW), Rejected)


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


def test_a_capability_without_an_operation_is_rejected() -> None:
    """Half a plan: what to look in, and nothing about what to do with it."""
    plan = Plan(a_capability_answers_it=True, capability="shuttle", operation=Operation())
    rejected = check(plan, NOW)
    assert isinstance(rejected, Rejected)
    assert "operation" in rejected.reason


def test_a_direction_alone_is_not_an_operation() -> None:
    """It has a default, so it is set on every plan whether meant or not."""
    plan = Plan(
        a_capability_answers_it=True,
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
    assert isinstance(check(Plan(specific_to_ramapo=True), NOW), Rejected)
    assert isinstance(
        check(Plan(specific_to_ramapo=True, topic="overnight guest policy"), NOW), Plan
    )


def test_general_needs_nothing() -> None:
    assert isinstance(check(Plan(), NOW), Plan)


def test_safety_survives_a_plan_that_would_otherwise_be_rejected() -> None:
    """The turns that most need an answer are the ones that must not be rejected.

    A CODE plan naming no capability is rejected on every other turn. Carrying
    a concern it is not, because what Python does about a concern depends on
    no lookup.
    """
    assert isinstance(check(Plan(a_capability_answers_it=True), NOW), Rejected)
    checked = check(Plan(safety=[Concern.EMERGENCY], a_capability_answers_it=True), NOW)
    assert isinstance(checked, Plan)
    assert checked.safety == [Concern.EMERGENCY]


def test_every_concern_is_carried_through_the_check_not_just_the_first() -> None:
    """Every branch of `check` rebuilds the plan, which is how a list goes missing."""
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
        "routing": {"CODE?": "Yes", "RAMAPO?": "—", "ROUTE": "CODE"},
        "capability": "shuttle",
        "filters": {"destination": "Garden State Plaza", "date": "2031-03-06"},
        "operation": {"orderBy": "departureTime", "direction": "ascending", "limit": 1},
    }


def test_an_unused_half_of_the_plan_is_not_in_the_summary() -> None:
    checked = check(Plan(), NOW)
    assert checked.summary() == {  # type: ignore[union-attr]
        "routing": {"CODE?": "No", "RAMAPO?": "No", "ROUTE": "GENERAL"},
        "freshness": "stable",
    }


def test_the_summary_shows_why_the_lane_was_chosen() -> None:
    """The two judgements are the reasoning, so they are in the log beside it."""
    checked = check(Plan(specific_to_ramapo=True, topic="parking"), NOW)
    assert isinstance(checked, Plan)
    routing = checked.summary()["routing"]
    assert list(routing) == ["CODE?", "RAMAPO?", "ROUTE"], "the questions, then the answer"
    assert routing == {"CODE?": "No", "RAMAPO?": "Yes", "ROUTE": "RAG"}


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


def test_the_planner_is_not_asked_where_the_answer_comes_from() -> None:
    """`lane` is Python's, derived from the two answers, so it is not on the wire.

    It used to be a third thing the planner said, and it could disagree with
    the reasoning that produced it — a plan whose lane contradicted its own
    judgement was a state the inspector could render. Off the schema, that
    state is unrepresentable rather than unobserved.
    """
    assert "lane" not in to_strict_json_schema(Plan)["properties"]
    assert "Lane" not in to_strict_json_schema(Plan).get("$defs", {})


def test_the_instruction_names_no_campus_thing() -> None:
    """A worked example in the prompt is the first step back to an intent list.

    This asserted the prompt held no `?` at all, which was a proxy for that and
    is no longer one: the instruction now asks the model two questions outright,
    and those are the opposite of a worked example. What it was always aiming at
    is below — nothing a student would recognise as their own question.
    """
    for thing in ("shuttle", "menu", "registrar", "dining", "parking", "garden state"):
        assert thing not in PLAN.lower(), f"the instruction names {thing!r}"


def test_every_stage_loads_its_instruction_from_disk() -> None:
    """A prompt.md that goes missing should fail at startup, not on a turn."""
    for instruction in (UNDERSTAND, PLAN, ANSWER):
        assert instruction and not instruction.startswith("#")


def test_every_model_instruction_is_a_prompt_md() -> None:
    """The rule, enforced rather than remembered.

    Any module that names `instructions=` is sending a prompt, and the value
    must have come off disk. A new stage that inlines its instruction as a
    Python string fails here, which is the only reliable moment to catch it.
    """
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
    """No header, no notes, no section stripped on the way out.

    What the file says is what the model is sent. Any rule for subtracting part
    of it is one more difference between what a prompt reads as and what it
    does — which is the reason these are not Python in the first place.
    """
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
    """The planner did this four times in five. Python does it five times in five."""
    assert anchor("population of France", NOW) == f"population of France as of {NOW:%Y-%m-%d}"


def test_the_date_is_added_with_no_condition_on_the_planners_wording() -> None:
    """A rule that dates only sometimes has to be right about when. This one does not."""
    for query in ("who won the 2018 world cup", "price of milk", "current price of gold"):
        assert anchor(query, NOW) == f"{query} as of {NOW:%Y-%m-%d}"


def test_a_date_the_planner_wrote_anyway_is_replaced_not_doubled() -> None:
    """Told the time and asked for a search, it copies the date about a third of the time."""
    stamp = f"as of {NOW:%Y-%m-%d}"
    assert anchor(f"population of France {stamp}", NOW) == f"population of France {stamp}"
    assert anchor("population of France as of the most recent data", NOW) == (
        f"population of France {stamp}"
    )


def test_words_of_meaning_are_not_mistaken_for_a_date() -> None:
    """`current` is what the search is for, not when it is for."""
    assert anchor("current president of France", NOW).startswith("current president of France")


def test_a_current_plan_carries_both_the_meaning_and_the_dated_query() -> None:
    """Which of the two was at fault is the first thing worth knowing."""
    checked = check(Plan(freshness="current", query="population of France"), NOW)
    assert isinstance(checked, Plan)
    assert checked.query == "population of France", "the planner's meaning is kept as written"
    assert checked.effective_query == f"population of France as of {NOW:%Y-%m-%d}"
    assert checked.summary()["effectiveQuery"] == checked.effective_query


def test_the_second_question_is_blank_when_the_cascade_never_reached_it() -> None:
    """`RAMAPO?` sits on the `No` branch. When CODE wins it was never asked.

    The model still has to fill the field — a structured response cannot leave
    one unanswered — and with nothing reading it, what it fills in is noise:
    the same question twice gave Yes and then No. Printing that beside a real
    answer made it look like it meant something.
    """
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
    """The bound was 50, so "show me 100 courses" had no way to be expressed.

    The planner could not say a hundred, so it said one, and the answer was "I
    can only provide details on one course". A bound the question cannot reach
    does not narrow the answer, it corrupts it.
    """
    schema = to_strict_json_schema(Plan)["$defs"]["Operation"]["properties"]["limit"]
    allowed = next(one for one in schema["anyOf"] if one.get("type") == "integer")
    assert allowed["maximum"] == MOST_ROWS
    assert MOST_ROWS >= 100, "a question can plausibly ask for a hundred of something"


def test_the_two_ceilings_are_one_number() -> None:
    """What a plan may ask for and what may be handed over are one constraint.

    Two numbers would let a plan ask for more than the writer can be given, and
    the extra would vanish somewhere below without being reported.
    """
    assert GROUNDING_ROWS == MOST_ROWS
