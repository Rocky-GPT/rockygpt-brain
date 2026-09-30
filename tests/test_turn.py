"""Turn intake: a valid request makes a clean Turn; an invalid one never gets that far."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.turn import CAMPUS_TZ, Turn, intake


def ask(*messages: tuple[str, str]) -> ChatRequest:
    return ChatRequest.model_validate(
        {"messages": [{"role": r, "content": t} for r, t in messages]}
    )


def test_a_message_becomes_a_turn() -> None:
    turn = intake(ask(("user", "hi")), now=datetime(2026, 9, 30, 2, 45, tzinfo=UTC))
    assert turn == Turn("hi", turn.conversation_id, turn.request_id, turn.campus_now)
    assert turn.campus_now.isoformat() == "2026-09-29T22:45:00-04:00"
    assert turn.conversation_id and turn.request_id
    assert turn.conversation_id != turn.request_id


def test_the_message_is_the_latest_student_message_as_sent() -> None:
    turn = intake(ask(("user", "one"), ("assistant", "two"), ("user", " three\n")))
    assert turn.message == " three\n"


def test_each_turn_gets_its_own_ids() -> None:
    first, second = intake(ask(("user", "hi"))), intake(ask(("user", "hi")))
    assert first.request_id != second.request_id
    assert first.conversation_id != second.conversation_id


def test_the_same_conversation_keeps_its_conversation_id() -> None:
    turns = [intake(ask(("user", "hi")), "abc_123-X") for _ in range(3)]
    assert {turn.conversation_id for turn in turns} == {"abc_123-X"}
    assert len({turn.request_id for turn in turns}) == 3


def test_many_turns_all_get_different_request_ids() -> None:
    turns = [intake(ask(("user", "hi")), "same") for _ in range(5000)]
    assert len({turn.request_id for turn in turns}) == 5000


def test_the_turn_cannot_be_changed() -> None:
    with pytest.raises(AttributeError):
        intake(ask(("user", "hi"))).message = "other"  # type: ignore[misc]


@pytest.mark.parametrize(("utc", "campus"), [
    (datetime(2026, 7, 1, 2, 0, 0, 999, tzinfo=UTC), "2026-06-30T22:00:00-04:00"),
    (datetime(2026, 12, 1, 3, 0, tzinfo=UTC), "2026-11-30T22:00:00-05:00"),
    (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), "2026-11-01T01:30:00-04:00"),
    (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), "2026-11-01T01:30:00-05:00"),
])
def test_the_clock_is_new_york_time(utc: datetime, campus: str) -> None:
    assert intake(ask(("user", "hi")), now=utc).campus_now.isoformat() == campus


def test_the_clock_defaults_to_now() -> None:
    before = datetime.now(UTC).replace(microsecond=0)
    now = intake(ask(("user", "hi"))).campus_now
    assert now.tzinfo is CAMPUS_TZ
    assert before <= now <= datetime.now(UTC)


def message(role: str = "user", content: Any = "hi") -> dict[str, Any]:
    return {"role": role, "content": content}


@pytest.mark.parametrize("payload", [
    {},
    {"messages": []},
    {"messages": "hi"},
    {"messages": [message()] * 79 + [message("assistant"), message()]},
    {"messages": [message("assistant")]},
    {"messages": [message(), message("assistant")]},
    {"messages": [message("system")]},
    {"messages": [{"role": "user"}]},
    {"messages": [message(content="")]},
    {"messages": [message(content=" \n\t")]},
    {"messages": [message(content=5)]},
    {"messages": [message(content="x" * 16_001)]},
    {"messages": [message(content="x" * 10_000), message("assistant", "x" * 10_000)] * 2
     + [message(content="x" * 10_000)]},
    {"messages": [{**message(), "name": "Dan"}]},
    {"messages": [message()], "omittedMessages": -1},
    {"messages": [message()], "omittedMessages": 100_001},
    {"messages": [message()], "omittedMessages": 1.5},
    {"messages": [message()], "omittedMessages": True},
    {"messages": [message()], "omittedMessages": "3"},
    {"messages": [message()], "other": 1},
])
def test_a_request_that_doesnt_fit_is_refused(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(payload)


@pytest.mark.parametrize("payload", [
    {"messages": [message()]},
    {"messages": [message(), message("assistant"), message()]},
    {"messages": [message()] + [message("assistant"), message()] * 39},
    {"messages": [message(content="x" * 16_000), message("assistant", "x" * 16_000),
                  message(content="x" * 16_000)]},
    {"messages": [message()], "omittedMessages": 0},
    {"messages": [message()], "omittedMessages": 100_000},
])
def test_a_request_that_fits_is_accepted(payload: dict[str, Any]) -> None:
    ChatRequest.model_validate(payload)
