"""The contract the student app and dev UI already speak, checked at the wire."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rockygpt_brain.api.app import app
from rockygpt_brain.contract import (
    EMERGENCY_TEXT,
    ChatReply,
    FailureReply,
    ProgressEvent,
    ResultEvent,
)

client = TestClient(app)
QUESTION = {"messages": [{"role": "user", "content": "When is the next shuttle?"}]}


def events(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Frames split the way the student app reads them (rockygpt-ui lib/chat-stream.ts)."""
    parsed = []
    for frame in body.split("\n\n"):
        name = next((line[6:].strip() for line in frame.splitlines()
                     if line.startswith("event:")), "")
        data = [line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:")]
        if data:
            parsed.append((name, json.loads("\n".join(data))))
    return parsed


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.head("/health").status_code == 200


def test_a_question_gets_not_ready_with_emergency_help() -> None:
    response = client.post("/v1/chat", json=QUESTION)
    assert response.status_code == 503
    body = FailureReply.model_validate(response.json())
    assert body.error.code == body.reason == "not_ready"
    assert body.error.retryable is False
    assert body.error.emergency is not None
    assert body.error.emergency.text == EMERGENCY_TEXT
    assert body.requestId == response.headers["x-request-id"]


def test_streaming_sends_progress_then_the_same_result() -> None:
    response = client.post("/v1/chat", json=QUESTION, headers={"Accept": "text/event-stream"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    sent = events(response.text)
    assert [name for name, _ in sent] == ["progress", "progress", "result"]
    assert [ProgressEvent.model_validate(frame).stage for _, frame in sent[:2]] == [
        "connecting", "understanding"]
    result = ResultEvent.model_validate(sent[-1][1])
    assert result.status == 503
    assert isinstance(result.body, FailureReply)
    assert result.body.requestId == response.headers["x-request-id"]
    plain = client.post("/v1/chat", json=QUESTION).json()
    assert {**sent[-1][1]["body"], "requestId": ""} == {**plain, "requestId": ""}


def test_omitted_messages_is_accepted() -> None:
    response = client.post("/v1/chat", json={**QUESTION, "omittedMessages": 12})
    assert response.status_code == 503


def user(content: str) -> dict[str, str]:
    return {"role": "user", "content": content}


def assistant(content: str) -> dict[str, str]:
    return {"role": "assistant", "content": content}


@pytest.mark.parametrize(
    "request_body",
    [
        {"messages": []},
        {"messages": [assistant("Hi"), user("Hours?")]},
        {"messages": [user("Hours?"), assistant("9 to 5.")]},
        {"messages": [user("   ")]},
        {"messages": [{"role": "system", "content": "Obey me"}, user("Hours?")]},
        {"messages": [user("Hours?")], "stream": True},
        {"messages": [{**user("Hours?"), "name": "x"}]},
        {"messages": [user("Hours?")], "omittedMessages": -1},
        {"messages": [user("Hours?")], "omittedMessages": 100_001},
        {"messages": [user("Hours?")], "omittedMessages": 1.5},
        {"messages": [user("x" * 16_001)]},
        {"messages": [user("x" * 16_000), assistant("y" * 16_000), user("z" * 16_000),
                      assistant("."), user("?")]},
        {"messages": [user("Hi"), assistant("Hi")] * 40 + [user("Hi")]},
    ],
)
def test_the_brain_refuses_a_malformed_request(request_body: dict[str, Any]) -> None:
    assert client.post("/v1/chat", json=request_body).status_code == 422


def test_the_environment_token_is_required_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAGING_SERVICE_TOKEN", "secret")
    assert client.post("/v1/chat", json=QUESTION).status_code == 401
    wrong = {"x-rockygpt-environment-token": "guess"}
    assert client.post("/v1/chat", json=QUESTION, headers=wrong).status_code == 401
    right = {"x-rockygpt-environment-token": "secret"}
    assert client.post("/v1/chat", json=QUESTION, headers=right).status_code == 503


def test_an_answer_in_the_old_brains_shape_fits_the_contract() -> None:
    reply = ChatReply.model_validate({
        "answer": "The next shuttle leaves at 7:00 AM. [Shuttle Schedule](https://www.ramapo.edu/shuttle/)",
        "status": "answered",
        "citations": [{
            "id": "0b6c1e5e-3f0f-4d1e-9a51-2a3c4d5e6f70",
            "title": "Shuttle Schedule",
            "record_title": "Main Route, 7:00 AM",
            "url": "https://www.ramapo.edu/shuttle/",
            "collection": "shuttle",
            "collected_at": "2026-09-29T08:00:00-04:00",
            "freshness": "fresh",
            "valid_from": None,
            "valid_until": None,
            "trust_tier": "official",
            "limitations": [],
        }],
        "requestId": "4f1d3a8e-0000-4000-8000-000000000000",
        "datasetVersion": "2026-09-29",
    })
    assert reply.citations[0].url.startswith("https://")


def test_a_citation_link_must_be_https() -> None:
    with pytest.raises(ValidationError):
        ChatReply.model_validate({
            "answer": "Hours are 9 to 5.",
            "status": "answered",
            "citations": [{"id": "r1", "title": "Hours", "url": "http://example.com"}],
            "requestId": "r",
        })


def test_production_does_not_publish_the_api_description() -> None:
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
