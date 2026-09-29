"""Danger help comes first, every failure helps, and logs never hold the student's words."""

import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.contract import EMERGENCY_TEXT, RETRYABLE, ChatReply, FailureReply
from rockygpt_brain.failures import FAILURES, failure
from rockygpt_brain.safety import SAFETY_TEXT, said_danger

client = TestClient(app)


def ask(question: str, **headers: str) -> Any:
    return client.post("/v1/chat", json={"messages": [{"role": "user", "content": question}]},
                       headers=headers)


@pytest.mark.parametrize(("question", "kind"), [
    ("My roommate is NOT breathing!!", "danger"),
    ("someone passed out and isn't waking up", "danger"),
    ("Someone can’t breathe in Birch", "danger"),
    ("i want to kill myself", "self_harm"),
    ("I don't want to be alive anymore", "self_harm"),
    ("When does Birch close?", None),
    ("I'm dying to try the new pizza", None),
    ("Is there a breathing exercise class?", None),
])
def test_danger_is_read_from_the_question(question: str, kind: str | None) -> None:
    assert said_danger(question) == kind


def test_danger_gets_the_safety_help_first() -> None:
    response = ask("my friend is unconscious what do i do")
    assert response.status_code == 200
    reply = ChatReply.model_validate(response.json())
    assert reply.status == "partial"
    assert reply.answer.startswith(SAFETY_TEXT["danger"])


def test_a_streaming_app_gets_the_safety_help_before_the_answer() -> None:
    response = ask("I might hurt myself tonight", accept="text/event-stream")
    frames = [json.loads(line[5:]) for line in response.text.splitlines()
              if line.startswith("data:")]
    assert "safety" not in frames[0]
    assert frames[1]["safety"]["answer"] == SAFETY_TEXT["self_harm"]
    assert frames[-1]["status"] == 200
    assert frames[-1]["body"]["answer"].startswith(SAFETY_TEXT["self_harm"])


@pytest.mark.parametrize("code", sorted(FAILURES))
def test_every_failure_has_a_status_a_message_and_the_emergency_help(code: str) -> None:
    status, body = failure(code, "r")  # type: ignore[arg-type]
    assert 400 <= status <= 599
    assert FailureReply.model_validate(body.model_dump()).error.message
    assert body.error.retryable == (code in RETRYABLE)
    if code == "request_cancelled":
        assert body.error.emergency is None
    else:
        assert body.error.emergency is not None
        assert body.error.emergency.text == EMERGENCY_TEXT


def test_a_spent_allowance_says_when_it_comes_back() -> None:
    status, body = failure("budget_exhausted", "r", reset_at="2026-10-01T04:00:00Z")
    assert status == 429
    assert body.error.resetAt == "2026-10-01T04:00:00Z"
    assert body.error.retryable is False


def test_the_turn_log_never_holds_the_students_words(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        ask("My student ID is 1234567 and my friend is not breathing")
    lines = [record.getMessage() for record in caplog.records
             if record.getMessage().startswith("brain_turn ")]
    assert len(lines) == 1
    assert "1234567" not in lines[0]
    assert "breathing" not in lines[0]
    assert json.loads(lines[0].removeprefix("brain_turn "))["safety"] is True
