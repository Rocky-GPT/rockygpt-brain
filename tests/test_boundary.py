"""The immediate-danger phrase floor; ordinary intent belongs to the assistant."""

from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.boundary import (
    SAFETY_MESSAGE,
    SAFETY_TEXTS,
    SITUATIONS,
    BoundaryResult,
    check,
    safety_text,
    situation_of,
)
from rockygpt_brain.turn import CAMPUS_TZ, Turn

CONTINUE = BoundaryResult("continue", "")


def read(message: str) -> BoundaryResult:
    return check(Turn(message, "conversation", "request", datetime.now(CAMPUS_TZ)))


def is_safety(message: str) -> bool:
    return read(message).kind == "safety"


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
    assert is_safety(message)


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
    assert is_safety(message)


@pytest.mark.parametrize("message", [
    "I CAN'T BREATHE", "i cant breathe", "I can’t  breathe.", "I can´t breathe",
    "i c\u200ban't breathe", "ｓｏｍｅｏｎｅ ｃｏｌｌａｐｓｅｄ", "SUİCİDE", "someone collápsed",
    "... I can't breathe",
])
def test_spelling_of_the_same_words_takes_the_same_path(message: str) -> None:
    assert read(message).situation == "medical" or read(message).situation == "self_harm"


def test_only_the_latest_message_is_read() -> None:
    assert read("what time is the shuttle") == CONTINUE


@pytest.mark.parametrize(("message", "situation"), [
    ("my friend isn't breathing", "medical"),
    ("someone collapsed in the caf", "medical"),
    ("she is having a seizure", "medical"),
    ("i'm bleeding badly", "medical"),
    ("I want to kill myself", "self_harm"),
    ("i think i want to die", "self_harm"),
    ("i dont want to be here anymore, thinking about ending it all", "self_harm"),
    ("someone has a gun", "danger"),
    ("there is an active shooter", "danger"),
    ("some guy is following me and won't leave me alone", "danger"),
    ("I was sexually assaulted last night", "danger"),
    ("someone broke into my room", "danger"),
    ("there is a fire in the dorm", "fire"),
    ("I smell smoke in Birch Hall", "fire"),
    ("there is a gas leak", "fire"),
    ("my roommate overdosed", "other"),
    ("I NEED AN AMBULANCE", "other"),
    ("this is an emergency", "other"),
    # Several kinds in one message: the general text, never a guess between them.
    ("my friend collapsed and someone has a gun", "other"),
    ("there is a fire and she isn't breathing", "other"),
    ("i want to kill myself, there is smoke everywhere", "other"),
])
def test_each_kind_of_danger_gets_its_own_help(message: str, situation: str) -> None:
    result = read(message)
    assert result == BoundaryResult("safety", SAFETY_TEXTS[situation], situation)


def test_the_replies_are_fixed_text() -> None:
    assert SITUATIONS == tuple(SAFETY_TEXTS)
    # The general text is the old one: both numbers, and who can and can't help.
    assert "911" in SAFETY_MESSAGE and "988" in SAFETY_MESSAGE
    assert SAFETY_MESSAGE == SAFETY_TEXTS["other"] == safety_text(None) == safety_text("other")
    assert read("show me my grades") == CONTINUE
    assert read("hi").message == ""
    assert len(set(SAFETY_TEXTS.values())) == len(SAFETY_TEXTS)


def test_every_text_sends_the_student_to_the_right_number_and_admits_its_limits() -> None:
    for name, text in SAFETY_TEXTS.items():
        assert "RockyGPT can't send help or stay with you" in text, name
        assert "911" in text, name
        assert "\u2014" not in text and "\u2013" not in text, name
    assert "988" in SAFETY_TEXTS["self_harm"]
    # Only the texts about hurting yourself, and the general one, mention the crisis line.
    for name in ("medical", "danger", "fire"):
        assert "988" not in SAFETY_TEXTS[name], name
    assert SAFETY_TEXTS["self_harm"].index("988") < SAFETY_TEXTS["self_harm"].index("911")
    for name in ("medical", "danger", "fire"):
        assert SAFETY_TEXTS[name].index("911") < 140, name


def test_a_reply_is_traced_back_to_its_text() -> None:
    for name, text in SAFETY_TEXTS.items():
        assert situation_of(text) == name
        assert situation_of(text + "\n\nOn campus: ...") == name
    assert situation_of("Hi! I'm RockyGPT.") is None


def ask(client: TestClient, text: str) -> tuple[int, dict[str, Any]]:
    response = client.post("/v1/chat", json={"messages": [{"role": "user", "content": text}]})
    assert response.headers["x-request-id"] == response.json()["requestId"]
    return response.status_code, response.json()


def test_a_danger_message_gets_a_200_with_the_safety_help(client: TestClient) -> None:
    status, body = ask(client, "Someone collapsed")
    assert status == 200
    assert body["status"] == "partial"
    assert body["answer"] == SAFETY_TEXTS["medical"]
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
    assert is_safety("I took too many pills")
