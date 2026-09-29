"""The conversation is read once, in order, with what's missing marked."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from rockygpt_brain.context import CAMPUS_TIMEZONE, read_context
from rockygpt_brain.contract import ChatRequest

NOW = datetime(2026, 9, 29, 22, 5, tzinfo=CAMPUS_TIMEZONE)


def conversation(*contents: str, omitted: int = 0) -> ChatRequest:
    roles = ("user", "assistant")
    return ChatRequest.model_validate({
        "messages": [{"role": roles[index % 2], "content": content}
                     for index, content in enumerate(contents)],
        "omittedMessages": omitted,
    })


def test_a_first_question() -> None:
    context = read_context(conversation("When does Birch close?"), NOW)
    assert context.question == "When does Birch close?"
    assert context.earlier == ()
    assert context.first_question
    assert context.history_complete
    assert context.previous_answer is None
    assert context.message_number == 1


def test_a_follow_up_keeps_every_earlier_message_in_order() -> None:
    context = read_context(conversation(
        "When does Birch close?", "Birch closes at 11 PM.", "What about tomorrow?"), NOW)
    assert context.question == "What about tomorrow?"
    assert [message.content for message in context.earlier] == [
        "When does Birch close?", "Birch closes at 11 PM."]
    assert context.previous_answer == "Birch closes at 11 PM."
    assert not context.first_question
    assert context.message_number == 3


def test_left_out_messages_make_the_history_incomplete() -> None:
    context = read_context(conversation("And on Sunday?", omitted=18), NOW)
    assert not context.history_complete
    assert not context.first_question
    assert context.previous_answer is None
    assert context.message_number == 19


def test_the_clock_is_campus_time() -> None:
    utc = datetime(2026, 9, 30, 2, 5, tzinfo=UTC)
    assert read_context(conversation("Hi"), utc).now == NOW
    assert read_context(conversation("Hi"), utc).now.tzinfo == CAMPUS_TIMEZONE


def test_a_clock_without_a_time_zone_is_refused() -> None:
    with pytest.raises(ValueError):
        read_context(conversation("Hi"), datetime(2026, 9, 29, 22, 5))


def test_no_step_can_change_the_context() -> None:
    context = read_context(conversation("Hi"), NOW)
    with pytest.raises(FrozenInstanceError):
        context.question = "Something else"  # type: ignore[misc]
