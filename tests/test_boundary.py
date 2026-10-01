"""The immediate-danger phrase floor; ordinary intent belongs to the assistant."""

from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.boundary import SAFETY_MESSAGE, BoundaryResult, check
from rockygpt_brain.turn import CAMPUS_TZ, Turn

SAFETY = BoundaryResult("safety", SAFETY_MESSAGE)
CONTINUE = BoundaryResult("continue", "")


def read(message: str) -> BoundaryResult:
    return check(Turn(message, "conversation", "request", datetime.now(CAMPUS_TZ)))


@pytest.mark.parametrize("message", [
    "Someone collapsed",
    "my friend isn't breathing",
    "I can’t breathe!!",
    "he is unconscious and not responding",
    "I want to kill myself",
    "i think i want to die",
    "my roommate overdosed",
    "she is having a seizure",
    "there is a fire in the dorm",
    "I smell smoke in Birch Hall",
    "someone has a gun",
    "there is an active shooter",
    "some guy is following me and won't leave me alone",
    "I was sexually assaulted last night",
    "i'm bleeding badly",
    "I NEED AN AMBULANCE",
    "this is an emergency",
])
def test_danger_gets_the_safety_help(message: str) -> None:
    assert read(message) == SAFETY


@pytest.mark.parametrize("message", [
    "Show me my grades",
    "what's my GPA",
    "What is my balance?",
    "when is my tuition bill due for MY account",
    "do I have any holds",
    "am I registered for next semester",
    "Register me for CS 450",
    "can you please drop my calculus class",
    "Pay my tuition",
    "add me to the waitlist for BIOL 101",
    "could you reset my password",
    "email my professor for me",
    "hey please send my transcript to Rutgers",
    "i need you to submit my application",
])
def test_account_words_do_not_discard_public_parts(message: str) -> None:
    assert read(message) == CONTINUE


@pytest.mark.parametrize("message", [
    "What's the next shuttle?",
    "hi",
    "thanks!",
    "",
    "asdf",
    "where is the Learning Commons",
    "When does registration open?",
    "what is the drop deadline",
    "How do I check my grades?",
    "how can i pay my tuition",
    "where do I find my transcript",
    "how to kill a python process",
    "what is the fire safety policy",
    "when is the CPR class",
    "what number do I call for campus safety",
    "the fall break schedule",
    "does the shuttle run when it's snowing",
    "professor Smith's office hours",
    "when is the suicide prevention club meeting",
    "is there an active shooter training",
    "what happens if there is a fire drill during class",
    "is there a seizure awareness workshop",
])
def test_everything_else_continues(message: str) -> None:
    assert read(message) == CONTINUE


@pytest.mark.parametrize("message", [
    "show me my grades, I want to die",
    "Register me for CS 450 there is a fire in the building",
])
def test_danger_wins_over_an_account_request(message: str) -> None:
    assert read(message) == SAFETY


@pytest.mark.parametrize("message", [
    "I CAN'T BREATHE", "i cant breathe", "I can’t  breathe.", "I can´t breathe",
    "i c\u200ban't breathe", "ｓｏｍｅｏｎｅ ｃｏｌｌａｐｓｅｄ", "SUİCİDE", "someone collápsed",
    "... I can't breathe",
])
def test_spelling_of_the_same_words_takes_the_same_path(message: str) -> None:
    assert read(message) == SAFETY


def test_only_the_latest_message_is_read() -> None:
    assert read("what time is the shuttle") == CONTINUE


def test_the_replies_are_fixed_text() -> None:
    assert "911" in SAFETY_MESSAGE and "988" in SAFETY_MESSAGE
    assert read("someone collapsed").message == SAFETY_MESSAGE
    assert read("show me my grades") == CONTINUE
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


def test_account_request_needs_the_assistant_not_a_phrase_refusal(client: TestClient) -> None:
    status, body = ask(client, "Register me for CS 450")
    assert status == 503
    assert body["reason"] == "model_not_configured"


def test_unconfigured_service_reports_configuration_failure(client: TestClient) -> None:
    status, body = ask(client, "What's the next shuttle?")
    assert status == 503
    assert body["reason"] == "model_not_configured"


def test_normal_medication_mention_is_not_an_overdose() -> None:
    assert read("I took my medication this morning; where is the bookstore?") == CONTINUE
    assert read("I took too many pills") == SAFETY
