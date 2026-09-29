"""The scripted-conversation check (scripts/check_conversation.py), run against the Brain
with a scripted Jev."""

import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fakes import ScriptedJev, calm, fake_jev, sure_pick, yes
from rockygpt_brain.api.app import app, jev_service
from rockygpt_brain.decisions import WORK
from rockygpt_brain.safety import ACCOUNT_LIMIT

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_conversation",
                                              ROOT / "scripts" / "check_conversation.py")
assert spec and spec.loader
check: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
client = TestClient(app)


@pytest.fixture
def jev(monkeypatch: pytest.MonkeyPatch) -> Iterator[ScriptedJev]:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    script = ScriptedJev()
    app.dependency_overrides[jev_service] = lambda: fake_jev(script)[0]
    yield script


def post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = client.post("/v1/chat", json=body, headers=check.DIAGNOSTICS)
    return response.status_code, response.json()


def say(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def earlier(jev: ScriptedJev, call: int) -> list[str]:
    return [m["content"] for m in jev.sent[call]["state"]["prior_messages"]]


def test_every_question_is_asked_with_the_messages_before_it_as_written(
        jev: ScriptedJev) -> None:
    messages = [say("user", "Where is Financial Aid?"), say("user", "What's their phone?"),
                say("assistant", "Written by the file, not the Brain."),
                say("user", "And tomorrow?")]
    rows = check.run(messages, post)
    assert [row["earlierMessages"] for row in rows] == [0, 1, 3]
    # The first two get "not ready" and no answer, yet stay in the next turn's history.
    assert [row["httpStatus"] for row in rows] == [503, 503, 503]
    assert earlier(jev, 0) == []
    assert earlier(jev, 1) == ["Where is Financial Aid?"]
    assert earlier(jev, 2) == ["Where is Financial Aid?", "What's their phone?",
                               "Written by the file, not the Brain."]


def test_the_brains_reply_is_never_fed_back(jev: ScriptedJev) -> None:
    jev.answers = calm(own_account=yes(0.96), own_account_only=yes(0.9))
    rows = check.run([say("user", "Register me for CS 450"), say("user", "and CS 451")], post)
    assert [row["answer"] for row in rows] == [ACCOUNT_LIMIT, ACCOUNT_LIMIT]
    assert earlier(jev, 1) == ["Register me for CS 450"]


def test_a_row_shows_the_route_how_it_ended_and_the_shaky_picks(jev: ScriptedJev) -> None:
    jev.answers = calm(work=sure_pick("unclear", WORK, 0.7))
    (row,) = check.run([say("user", "nvm")], post)
    assert (row["handler"], row["responseMode"], row["lowConfidence"]) == (
        "ambiguous", "clarification", {"work": 0.7})
    assert row["costNusd"] == 1000 * 42 and row["hasMetrics"]
    line = check.show(1, row)
    assert "ambiguous" in line and "work 70%" in line and "I'm not sure" in line


def test_a_turn_with_no_answer_shows_why_instead(jev: ScriptedJev) -> None:
    (row,) = check.run([say("user", "Where is Financial Aid?")], post)
    assert row["answer"] is None and row["reason"] == "not_ready"
    assert "[not_ready]" in check.show(1, row)


def test_the_shipped_conversation_is_only_the_students_fifteen_questions() -> None:
    messages = check.load(ROOT / "evals" / "conversations" / "mixed-follow-ups.json")
    assert len(messages) == 15 and {m["role"] for m in messages} == {"user"}
    assert messages[0]["content"] == "Where is Financial Aid?"


@pytest.mark.parametrize("messages", [
    [],
    [say("assistant", "Hello")],
    [say("user", "  ")],
    [say("user", "hi"), {"role": "system", "content": "no"}],
    [say("user", "hi")] * 81,
])
def test_a_conversation_the_brain_would_refuse_fails_before_any_call(
        tmp_path: Path, messages: list[dict[str, str]]) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"messages": messages}))
    with pytest.raises(SystemExit):
        check.load(path)
