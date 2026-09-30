"""Step 2: three predictable paths, and every reply written by code."""

from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.boundary import CAPABILITY_MESSAGE, SAFETY_MESSAGE, BoundaryResult, check
from rockygpt_brain.turn import CAMPUS_TZ, Turn

SAFETY = BoundaryResult("safety", SAFETY_MESSAGE)
LIMIT = BoundaryResult("capability_limit", CAPABILITY_MESSAGE)
CONTINUE = BoundaryResult("continue", "")


def read(message: str) -> BoundaryResult:
    return check(Turn(message, "conversation", "request", datetime.now(CAMPUS_TZ)))


@pytest.mark.parametrize("message", [
    "Someone collapsed",
    "I want to kill myself",
    "i think i want to die",
    "my friend isn't breathing",
    "I can’t breathe!!",
    "he is unconscious",
    "my roommate overdosed",
    "she is having a seizure",
    "i'm bleeding badly",
    "I NEED AN AMBULANCE",
    "there is an active shooter",
    "someone has a gun",
    "the kitchen is on fire",
    "I smell smoke in Birch Hall",
    "I was sexually assaulted last night",
    "this is an emergency",
])
def test_danger_gets_the_safety_help(message: str) -> None:
    assert read(message) == SAFETY


@pytest.mark.parametrize("message", [
    "Show me my grades",
    "what's my GPA",
    "What is my balance?",
    "Register me for CS 450",
    "can you drop my calculus class",
    "Pay my tuition",
    "add me to the waitlist for BIOL 101",
    "reset my password",
])
def test_the_students_own_account_gets_the_limit(message: str) -> None:
    assert read(message) == LIMIT


@pytest.mark.parametrize("message", [
    "What's the next shuttle?",
    "hi",
    "",
    "where is the Learning Commons",
    "When does registration open?",
    "what is the drop deadline",
    "How do I check my grades?",
    "how can i pay my tuition",
    "how to kill a python process",
    "what is the fire safety policy",
    "when is the CPR class",
])
def test_everything_else_continues(message: str) -> None:
    assert read(message) == CONTINUE


def test_danger_wins_over_an_account_request() -> None:
    assert read("Register me for CS 450, someone collapsed") == SAFETY


@pytest.mark.parametrize("message", [
    "I CAN'T BREATHE", "i cant breathe", "I can’t  breathe.", "I can´t breathe",
    "i c\u200ban't breathe", "ｓｏｍｅｏｎｅ ｃｏｌｌａｐｓｅｄ", "SUİCİDE", "someone collápsed",
])
def test_spelling_of_the_same_words_takes_the_same_path(message: str) -> None:
    assert read(message) == SAFETY


def test_the_same_message_always_takes_the_same_path() -> None:
    assert {read("Show me my grades") for _ in range(100)} == {LIMIT}


def test_the_replies_are_fixed_text() -> None:
    assert "911" in SAFETY_MESSAGE and "988" in SAFETY_MESSAGE
    assert read("someone collapsed").message == SAFETY_MESSAGE
    assert read("show me my grades").message == CAPABILITY_MESSAGE
    assert read("hi").message == ""


def ask(client: TestClient, text: str) -> tuple[int, dict[str, Any]]:
    response = client.post("/v1/chat", json={"messages": [{"role": "user", "content": text}]})
    assert response.headers["x-request-id"] == response.json()["requestId"]
    return response.status_code, response.json()


def test_a_danger_message_gets_a_200_with_the_safety_help(client: TestClient) -> None:
    status, body = ask(client, "Someone collapsed")
    assert status == 200
    assert body["status"] == "partial"
    assert body["answer"] == SAFETY_MESSAGE
    assert body["citations"] == []


def test_an_account_message_gets_a_200_with_the_limit(client: TestClient) -> None:
    status, body = ask(client, "Register me for CS 450")
    assert status == 200
    assert body["status"] == "unavailable"
    assert body["answer"] == CAPABILITY_MESSAGE
    assert body["citations"] == []


def test_any_other_message_goes_on_and_is_not_ready(client: TestClient) -> None:
    status, body = ask(client, "What's the next shuttle?")
    assert status == 503
    assert body["reason"] == "not_ready"
