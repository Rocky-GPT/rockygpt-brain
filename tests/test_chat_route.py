"""`POST /v1/chat` at the wire: what the apps see for a valid and an invalid request."""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api import app as app_module
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.turn import Turn, intake

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
PRIVATE = "my ID is 123-45-6789 please help"
HI = {"messages": [{"role": "user", "content": "hi"}]}


def test_a_valid_request_is_taken_in_as_a_turn(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    turns: list[Turn] = []

    def spy(request: ChatRequest) -> Turn:
        turns.append(intake(request))
        return turns[-1]

    monkeypatch.setattr(app_module, "intake", spy)
    payload = {"messages": [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "latest"},
    ], "omittedMessages": 12}
    response = client.post("/v1/chat", json=payload)
    body = response.json()
    assert [turn.message for turn in turns] == ["latest"]
    assert response.status_code == 503
    assert body["error"] == {
        "code": "not_ready", "message": "RockyGPT can't answer questions yet.",
        "retryable": False,
    }
    assert body["reason"] == "not_ready"
    assert body["requestId"] == turns[0].request_id
    assert UUID.match(body["requestId"])
    assert response.headers["x-request-id"] == body["requestId"]


INVALID: list[tuple[str, dict[str, Any]]] = [
    ("application/json", {"json": {"messages": []}}),
    ("application/json", {"json": {"messages": [{"role": "assistant", "content": "hi"}]}}),
    ("application/json", {"json": {"messages": [{"role": "user", "content": PRIVATE}],
                                   "omittedMessages": PRIVATE}}),
    ("application/json", {"json": {"messages": [{"role": "user", "content": PRIVATE,
                                                 "name": PRIVATE}]}}),
    ("application/json", {"json": {"messages": PRIVATE}}),
    ("application/json", {"json": [PRIVATE]}),
    ("application/json", {"json": PRIVATE}),
    ("application/json", {"json": None}),
    ("application/json", {"content": b"{not json"}),
    ("application/json", {"content": b""}),
    ("application/json", {"content": b'{"messages":[{"role":"user","content":"\xff"}]}'}),
    ("application/json", {"content": b"[" * 200_000}),
    ("application/json", {"content": b'{"omittedMessages":' + b"9" * 5000 + b"}"}),
    ("text/plain", {"content": PRIVATE.encode()}),
]


@pytest.mark.parametrize(("content_type", "kwargs"), INVALID)
def test_every_invalid_request_gets_the_same_error(
        client: TestClient, content_type: str, kwargs: dict[str, Any]) -> None:
    response = client.post("/v1/chat", headers={"content-type": content_type}, **kwargs)
    body = response.json()
    assert response.status_code == 422
    assert set(body) == {"error", "reason", "requestId", "detail"}
    assert body["error"] == {
        "code": "invalid_request",
        "message": "The request isn't in the shape RockyGPT expects.",
        "retryable": False,
    }
    assert body["reason"] == "invalid_request"
    assert UUID.match(body["requestId"])
    assert response.headers["x-request-id"] == body["requestId"]
    assert body["detail"]
    assert all(set(item) == {"type", "loc", "msg"} for item in body["detail"])
    assert "123-45-6789" not in response.text
    assert "{not json" not in response.text


def test_an_unknown_field_is_reported_the_way_the_student_app_looks_for(
        client: TestClient) -> None:
    """rockygpt-ui retries without omittedMessages when a 422 lists it as extra_forbidden."""
    detail = client.post("/v1/chat", json={**HI, "omittedMessages": 1, "x": 1}).json()["detail"]
    assert {"type": "extra_forbidden", "loc": ["body", "x"]} == {
        k: v for k, v in detail[0].items() if k != "msg"
    }


def test_other_errors_keep_fastapis_answer(client: TestClient) -> None:
    assert client.get("/v1/chat").status_code == 405
    assert client.get("/nowhere").status_code == 404


def test_the_probes_work(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/readiness").json() == {"status": "ready"}
