"""Step 5: every path, the order the rules run in, and the same input giving the same Plan."""

import pytest

from rockygpt_brain.context import Context, build_context
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.plan import Plan, build_plan
from rockygpt_brain.understanding import TOPICS, Understanding


def context(*lines: str) -> Context:
    return build_context(ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": line} for line in lines]}))


ALONE = context("What's the next shuttle?")
AFTER = context("What's the next shuttle?", "What about tomorrow?")


def understood(needs: str = "campus_info", topic: str = "transport", needs_history: bool = False,
               danger: bool = False, multi_part: bool = False) -> Understanding:
    return Understanding(needs, topic, needs_history, danger, multi_part)


@pytest.mark.parametrize(("understanding", "ctx", "expected"), [
    (understood(danger=True), ALONE, Plan("safety", "transport", False)),
    (understood("own_account", "academics"), ALONE, Plan("capability_limit", "academics", False)),
    (understood(multi_part=True), ALONE, Plan("multi_part", "transport", False)),
    (understood(needs_history=True), ALONE, Plan("clarify", "transport", False)),
    (understood("unclear", "none"), ALONE, Plan("clarify", "none", False)),
    (understood("campus_info"), ALONE, Plan("campus", "transport", False)),
    (understood("conversation", "none", needs_history=True), AFTER,
     Plan("conversation", "none", True)),
    (understood(needs_history=True), AFTER, Plan("campus", "transport", True)),
    (understood("outside", "none"), ALONE, Plan("general", "none", False)),
])
def test_each_path(understanding: Understanding, ctx: Context, expected: Plan) -> None:
    assert build_plan(understanding, ctx) == expected


def test_the_rules_run_in_order() -> None:
    everything = understood("own_account", needs_history=True, danger=True, multi_part=True)
    assert build_plan(everything, ALONE).path == "safety"
    assert build_plan(understood("own_account", multi_part=True, needs_history=True),
                      ALONE).path == "capability_limit"
    assert build_plan(understood("campus_info", multi_part=True, needs_history=True),
                      ALONE).path == "multi_part"
    assert build_plan(understood("unclear", multi_part=True), ALONE).path == "multi_part"
    assert build_plan(understood("unclear", needs_history=True), AFTER).path == "clarify"


def test_uses_history_only_when_history_exists() -> None:
    assert build_plan(understood(danger=True, needs_history=True), ALONE).uses_history is False
    assert build_plan(understood(danger=True, needs_history=True), AFTER).uses_history is True
    assert build_plan(understood(needs_history=False), AFTER).uses_history is False


def test_every_topic_is_carried_through() -> None:
    for topic in TOPICS:
        assert build_plan(understood(topic=topic), ALONE).topic == topic


def test_the_same_understanding_and_context_give_the_same_plan() -> None:
    assert build_plan(understood(needs_history=True), AFTER) == (
        build_plan(understood(needs_history=True), AFTER))
