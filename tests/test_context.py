"""Step 3: the context built from the conversation the app sent."""

import pytest

from rockygpt_brain.context import MAX_RECENT_MESSAGES, Context, build_context
from rockygpt_brain.contract import ChatMessage, ChatRequest


def talk(*lines: str, omitted: int = 0) -> ChatRequest:
    """A conversation that starts and ends with the student, and alternates after that."""
    roles = ["user" if i % 2 == 0 else "assistant" for i in range(len(lines))]
    return ChatRequest.model_validate({
        "messages": [{"role": r, "content": t} for r, t in zip(roles, lines, strict=True)],
        "omittedMessages": omitted,
    })


def said(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content)  # type: ignore[arg-type]


def test_no_history() -> None:
    assert build_context(talk("What's the next shuttle?")) == Context(
        latest_message="What's the next shuttle?",
        recent_messages=(),
        current_topic="shuttle",
        referenced_entities=(),
        history_available=False,
    )


def test_short_history_keeps_the_order_and_sets_the_latest_apart() -> None:
    context = build_context(talk("hi", "Hello! Ask me anything.", "where is the cafeteria"))
    assert context.latest_message == "where is the cafeteria"
    assert context.recent_messages == (
        said("user", "hi"), said("assistant", "Hello! Ask me anything.")
    )
    assert context.history_available


def test_a_follow_up_keeps_the_topic_of_the_earlier_question() -> None:
    context = build_context(talk(
        "What's the next shuttle?", "The next shuttle leaves at 3:15 PM.", "What about tomorrow?"
    ))
    assert context.latest_message == "What about tomorrow?"
    assert context.current_topic == "shuttle"
    assert context.history_available
    assert [m.content for m in context.recent_messages] == [
        "What's the next shuttle?", "The next shuttle leaves at 3:15 PM."
    ]


def test_a_follow_up_keeps_the_office_it_refers_to() -> None:
    context = build_context(talk(
        "Where is Financial Aid?", "Financial Aid is in Room 101.", "What's their phone number?"
    ))
    assert context.latest_message == "What's their phone number?"
    assert context.current_topic == "financial aid"
    assert context.referenced_entities == ("Financial Aid",)
    assert context.recent_messages == (
        said("user", "Where is Financial Aid?"), said("assistant", "Financial Aid is in Room 101.")
    )


def test_the_latest_topic_wins_over_an_earlier_one() -> None:
    context = build_context(talk("next shuttle?", "3:15 PM.", "where is the registrar?"))
    assert context.current_topic == "registrar"


def test_no_topic_when_nothing_names_one() -> None:
    context = build_context(talk("hi", "Hello!", "thanks"))
    assert context.current_topic is None
    assert context.referenced_entities == ()


def test_a_possessive_or_plural_still_names_it() -> None:
    context = build_context(talk("Financial Aid's phone number?", "555-0100.", "and the buses?"))
    assert context.referenced_entities == ("Financial Aid",)
    assert context.current_topic == "shuttle"


def test_what_the_assistant_said_never_sets_the_topic_or_entities() -> None:
    context = build_context(talk(
        "next shuttle?", "Ask the Registrar or Financial Aid about that.", "thanks"
    ))
    assert context.current_topic == "shuttle"
    assert context.referenced_entities == ()


def test_entities_come_oldest_first_once_each() -> None:
    context = build_context(talk(
        "Where is the Registrar?", "Here.", "and Financial Aid?", "There.", "registrar hours?",
        "Ok.", "and Financial Aid's?",
    ))
    assert context.referenced_entities == ("Registrar", "Financial Aid")


def test_only_the_newest_messages_are_kept_in_order() -> None:
    lines = [f"message {i}" for i in range(21)]  # 20 earlier messages, then the latest
    context = build_context(talk(*lines))
    assert [m.content for m in context.recent_messages] == lines[20 - MAX_RECENT_MESSAGES:20]
    assert context.latest_message == "message 20"


def test_a_topic_older_than_the_kept_messages_is_forgotten() -> None:
    lines = ["next shuttle?", *[f"message {i}" for i in range(MAX_RECENT_MESSAGES + 1)], "ok"]
    assert build_context(talk(*lines)).current_topic is None


def test_messages_the_app_left_out_are_not_history_we_have() -> None:
    context = build_context(talk("hi", omitted=5))
    assert context.recent_messages == ()
    assert not context.history_available


def test_the_same_history_gives_the_same_context() -> None:
    lines = ("Where is Financial Aid?", "It is in Room 101.", "What's their phone number?")
    assert build_context(talk(*lines)) == build_context(talk(*lines))


def test_the_request_is_left_as_it_was() -> None:
    request = talk("Where is Financial Aid?", "Room 101.", "phone?")
    before = request.model_copy(deep=True)
    build_context(request)
    assert request == before


def test_the_context_cannot_be_changed() -> None:
    with pytest.raises(AttributeError):
        build_context(talk("hi")).latest_message = "other"  # type: ignore[misc]
