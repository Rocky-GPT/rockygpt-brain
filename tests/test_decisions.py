"""What Jev decides about a turn, and what the student gets for it."""

import json
import logging
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fakes import MemoryLedger, ScriptedJev, calm, fake_jev, pick, sure_pick, yes
from rockygpt_brain.api.app import app, jev_service
from rockygpt_brain.context import CAMPUS_TIMEZONE, read_context
from rockygpt_brain.contract import ChatReply, ChatRequest, FailureReply
from rockygpt_brain.decisions import (
    NEEDS,
    QUESTIONS,
    ROUTES,
    WORK,
    Decisions,
    decide,
    handler,
)
from rockygpt_brain.jev import JevError, checked
from rockygpt_brain.safety import ACCOUNT_LIMIT, SAFETY_TEXT, Danger
from rockygpt_brain.turn import NOT_YET
from rockygpt_brain.work import revision

client = TestClient(app)
DANGER = pick("danger", {"self_harm": 0.02, "danger": 0.95, "none": 0.03})
SELF_HARM = pick("self_harm", {"self_harm": 0.9, "danger": 0.08, "none": 0.02})
ACCOUNT = {"own_account": yes(0.96), "own_account_only": yes(0.9)}


@pytest.fixture
def jev() -> Iterator[ScriptedJev]:
    """The app's Jev, answering calmly unless a test changes its script."""
    script = ScriptedJev()
    app.dependency_overrides[jev_service] = lambda: fake_jev(script)[0]
    yield script


def user(content: str) -> dict[str, str]:
    return {"role": "user", "content": content}


def ask(*messages: dict[str, str], **headers: str) -> Any:
    body = {"messages": list(messages) if messages else [user("When does Birch close?")]}
    return client.post("/v1/chat", json=body, headers=headers)


def frames(response: Any) -> list[dict[str, Any]]:
    return [json.loads(line[5:]) for line in response.text.splitlines()
            if line.startswith("data:")]


def test_jev_reads_the_question_apart_from_the_earlier_messages(jev: ScriptedJev) -> None:
    ask(user("What are the library's hours?"),
        {"role": "assistant", "content": "The library opens at 8 AM."}, user("And Sunday?"))
    (sent,) = jev.sent
    assert sent["questions"] == QUESTIONS
    assert sent["state"]["latest_request"] == "And Sunday?"
    assert [message["content"] for message in sent["state"]["prior_messages"]] == [
        "What are the library's hours?", "The library opens at 8 AM."]
    assert sent["state"]["campus_time"].endswith(("-04:00", "-05:00"))


def test_every_question_asks_about_the_students_words() -> None:
    for question in QUESTIONS.values():
        assert "latest_request" in json.dumps(question["instructions"])


def test_an_ordinary_question_still_gets_not_ready(jev: ScriptedJev) -> None:
    response = ask()
    assert response.status_code == 503
    assert FailureReply.model_validate(response.json()).reason == "not_ready"


def test_jev_reads_danger_the_phrases_miss(jev: ScriptedJev) -> None:
    jev.answers = calm(danger=DANGER)
    response = ask(user("my roommate took a whole bottle of pills"), accept="text/event-stream")
    sent = frames(response)
    assert "safety" not in sent[0] and "safety" not in sent[1]
    assert sent[2]["safety"]["answer"] == SAFETY_TEXT["danger"]
    assert sent[-1]["status"] == 200
    assert sent[-1]["body"]["answer"] == f"{SAFETY_TEXT['danger']}\n\n{NOT_YET}"


def test_the_phrases_still_catch_danger_when_jev_misses_it(jev: ScriptedJev) -> None:
    response = ask(user("what should I do if someone is unconscious"))
    assert ChatReply.model_validate(response.json()).answer.startswith(SAFETY_TEXT["danger"])


def test_self_harm_help_wins_when_jev_hears_more_than_the_phrases(jev: ScriptedJev) -> None:
    jev.answers = calm(danger=SELF_HARM)
    sent = frames(ask(user("I'm going to overdose on purpose"), accept="text/event-stream"))
    assert sent[1]["safety"]["answer"] == SAFETY_TEXT["danger"]
    assert sent[2]["safety"]["answer"] == SAFETY_TEXT["self_harm"]
    assert sent[-1]["body"]["answer"].startswith(SAFETY_TEXT["self_harm"])


def test_a_request_for_the_students_own_account_gets_what_rockygpt_cant_do(
        jev: ScriptedJev) -> None:
    jev.answers = calm(**ACCOUNT)
    response = ask(user("register me for CMPS 147"))
    assert response.status_code == 200
    reply = ChatReply.model_validate(response.json())
    assert (reply.answer, reply.status) == (ACCOUNT_LIMIT, "unavailable")


def test_an_account_pick_jev_isnt_sure_of_is_followed_and_marked(
        jev: ScriptedJev, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    jev.answers = calm(own_account=yes(0.7), own_account_only=yes(0.9))
    body = ask(user("register me for CMPS 147"), **{"x-rockygpt-diagnostics": "1"}).json()
    assert body["answer"] == ACCOUNT_LIMIT
    assert body["metrics"]["jev"]["decided"]["lowConfidence"] == {"ownAccount": 0.7}


def test_an_account_request_with_another_part_is_left_alone(jev: ScriptedJev) -> None:
    jev.answers = calm(own_account=yes(0.96), own_account_only=yes(0.2))
    assert ask(user("register me and where is the registrar?")).status_code == 503


@pytest.mark.parametrize(("needs_earlier", "status"), [(0.3, 200), (0.7, 503)])
def test_an_account_follow_up_that_leans_on_earlier_messages_is_left_alone(
        jev: ScriptedJev, needs_earlier: float, status: int) -> None:
    jev.answers = calm(**ACCOUNT, needs_earlier=yes(needs_earlier))
    response = ask(user("How do I drop a class?"),
                   {"role": "assistant", "content": "Use the registration page."},
                   user("Show me my grades"))
    assert response.status_code == status


def test_danger_and_an_account_request_get_both(jev: ScriptedJev) -> None:
    jev.answers = calm(danger=SELF_HARM, **ACCOUNT)
    reply = ChatReply.model_validate(ask(user("drop all my classes, I want to die")).json())
    assert reply.status == "partial"
    assert reply.answer == f"{SAFETY_TEXT['self_harm']}\n\n{ACCOUNT_LIMIT}"


@pytest.mark.parametrize("code", ["routing_timeout", "routing_invalid_response"])
def test_a_turn_goes_on_when_jev_fails(jev: ScriptedJev, code: str) -> None:
    jev.error = JevError(code)
    assert ask().status_code == 503
    assert ask(user("my friend is not breathing")).status_code == 200


def spending_refused(code: str) -> None:
    app.dependency_overrides[jev_service] = lambda: fake_jev(
        ScriptedJev(), MemoryLedger(refuse=code))[0]


def test_a_spent_allowance_stops_the_turn_and_says_when_it_returns() -> None:
    spending_refused("budget_exhausted")
    response = ask()
    assert response.status_code == 429
    failure = FailureReply.model_validate(response.json())
    assert failure.reason == "budget_exhausted"
    assert failure.error.resetAt == "2026-10-01T00:00:00-04:00"


@pytest.mark.parametrize(("code", "reason"), [
    ("accounting_unavailable", "accounting_unavailable"),
    ("accounting_paused", "accounting_paused"),
    ("accounting_bound_exceeded", "accounting_paused"),
])
def test_a_ledger_problem_stops_the_turn(code: str, reason: str) -> None:
    spending_refused(code)
    response = ask()
    assert response.status_code == 503
    assert response.json()["reason"] == reason


def test_danger_help_never_waits_on_money() -> None:
    spending_refused("budget_exhausted")
    response = ask(user("someone is having a seizure"))
    assert response.status_code == 200
    assert response.json()["answer"].startswith(SAFETY_TEXT["danger"])


def test_the_dev_ui_sees_jevs_readings_in_development(
        jev: ScriptedJev, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    jev.answers = calm(**ACCOUNT)
    metrics = ask(**{"x-rockygpt-diagnostics": "1"}).json()["metrics"]
    assert metrics["responseMode"] == "access_limit"
    assert metrics["routingCalls"] == 1
    assert metrics["jev"]["answers"]["own_account"] == {"yes": 0.96}
    assert metrics["jev"]["answers"]["danger"] == {
        "choice": "none", "probability": 0.97, "confidence": 0.95}
    assert metrics["jev"]["decided"] == {
        "danger": None, "ownAccount": True, "needsEarlier": False, "multiPart": False,
        "work": "look_up", "subject": "places", "named": "office", "needs": "campus_info",
        "reach": "supported", "handler": "account_action", "goesTo": "capability limit",
        "handlerPath": ["danger", "ownAccount"], "lowConfidence": {}}
    assert metrics["handler"] == "account_action"
    assert metrics["dangerPhrase"] is None
    assert metrics["jev"]["costNusd"] == 1000 * 42
    assert "Birch" not in json.dumps(metrics)
    jev.answers = calm()
    failed = ask(user("Hi"), **{"x-rockygpt-diagnostics": "1"})
    assert failed.status_code == 503 and "metrics" in failed.json()
    seizure = ask(user("someone is having a seizure"), **{"x-rockygpt-diagnostics": "1"})
    assert seizure.json()["metrics"]["dangerPhrase"] == "danger"


@pytest.mark.parametrize(("environment", "header"), [
    ("production", "1"), ("development", None), (None, "1")])
def test_diagnostics_stay_out_of_production_and_plain_requests(
        jev: ScriptedJev, monkeypatch: pytest.MonkeyPatch,
        environment: str | None, header: str | None) -> None:
    if environment:
        monkeypatch.setenv("BRAIN_ENVIRONMENT", environment)
    else:
        monkeypatch.delenv("BRAIN_ENVIRONMENT", raising=False)
    headers = {"x-rockygpt-diagnostics": header} if header else {}
    assert "metrics" not in ask(**headers).json()


def test_without_jev_the_turn_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    metrics = ask(**{"x-rockygpt-diagnostics": "1"}).json()["metrics"]
    assert metrics["jev"] == {"skipped": "routing_unavailable"}
    assert metrics["routingCalls"] == 0


def test_the_turn_log_has_jevs_numbers_and_no_student_words(
        jev: ScriptedJev, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        ask(user("What are my grades in CMPS 147?"))
    (line,) = [record.getMessage() for record in caplog.records
               if record.getMessage().startswith("brain_turn ")]
    logged = json.loads(line.removeprefix("brain_turn "))
    assert logged["jevCostNusd"] == 1000 * 42 and logged["responseMode"] == "not_ready"
    assert logged["handler"] == "campus_fact" and logged["lowConfidence"] == []
    assert "grades" not in line and "147" not in line


def test_jev_is_set_up_only_with_its_key_the_ledger_and_an_environment(
        monkeypatch: pytest.MonkeyPatch) -> None:
    settings = {"BRAIN_ENVIRONMENT": "development",
                "BRAIN_LEDGER_DATABASE_URL": "postgresql://unused",
                "BRAIN_TYPESAFE_API_KEY": "key"}
    for missing in settings:
        for name, value in settings.items():
            monkeypatch.setenv(name, value)
        monkeypatch.delenv(missing)
        jev_service.cache_clear()
        assert jev_service() is None
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    jev_service.cache_clear()
    assert jev_service() is not None
    jev_service.cache_clear()


def test_the_dev_ui_sees_which_brain_answered_and_who_did_the_work(
        jev: ScriptedJev, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    monkeypatch.setenv("BRAIN_REVISION", "d285c8f")
    revision.cache_clear()
    jev.answers = calm(**ACCOUNT)
    body = ask(user("register me for CMPS 147"), **{"x-rockygpt-diagnostics": "1"}).json()
    diagnostics = body["diagnostics"]
    assert diagnostics["brain"] == {"revision": "d285c8f", "environment": "development"}
    assert diagnostics["startedAt"].endswith(("-04:00", "-05:00"))
    work = diagnostics["work"]
    assert [step["stage"] for step in work["steps"]] == ["connecting", "understanding"]
    assert work["steps"][-1]["written"] == {"by": "code", "mode": "access_limit"}
    (call,) = work["calls"]
    assert (call["who"], call["what"], call["step"]) == ("jev", "routing", 1)
    assert work["steps"][1]["atMs"] <= call["startMs"] <= call["startMs"] + call["ms"]
    assert call["startMs"] + call["ms"] <= work["endMs"]
    assert "CMPS" not in json.dumps(diagnostics)
    revision.cache_clear()


def test_a_brain_run_from_a_checkout_names_its_commit_without_tags(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAIN_REVISION", raising=False)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    revision.cache_clear()
    found = revision()
    revision.cache_clear()
    assert found is not None and re.fullmatch(r"[0-9a-f]{40}(-dirty)?", found)


def test_the_work_record_marks_safety_and_a_failed_jev_call(
        jev: ScriptedJev, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    jev.error = JevError("routing_timeout")
    response = ask(user("my friend is not breathing"), accept="text/event-stream",
                   **{"x-rockygpt-diagnostics": "1"})
    work = frames(response)[-1]["body"]["diagnostics"]["work"]
    assert [step["stage"] for step in work["steps"]] == ["connecting", "understanding"]
    assert work["steps"][1]["safety"] is True
    assert work["steps"][1]["written"] == {"by": "code", "mode": "safety_net"}
    assert work["calls"][0]["failed"] is True
    failed = ask(user("hi"), **{"x-rockygpt-diagnostics": "1"})
    assert failed.status_code == 503 and "work" in failed.json()["diagnostics"]


def test_diagnostics_stay_out_of_production(
        jev: ScriptedJev, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "production")
    assert "diagnostics" not in ask(**{"x-rockygpt-diagnostics": "1"}).json()


def decided(**picks: Any) -> Decisions:
    """Jev's picks for an ordinary question ("Where is the Registrar?"), all sure, with
    `picks` on top."""
    ordinary = {"danger": None, "own_account": False, "needs_earlier": False,
                "work": "look_up", "subject": "places", "named": "office",
                "needs": "campus_info", "multi_part": False, "reach": "supported"}
    return Decisions(**{**ordinary, **picks}, sureness=dict.fromkeys(ordinary, 0.96))


@pytest.mark.parametrize(("picks", "said", "expected"), [
    ({"danger": "danger", "work": "policy"}, None, "danger"),
    ({"multi_part": True}, "danger", "danger"),
    ({"own_account": True, "multi_part": True}, None, "account_action"),
    ({"multi_part": True, "work": "cant_do"}, None, "multi_part"),
    ({"work": "calculate"}, None, "exact"),
    ({}, None, "campus_fact"),
    ({"work": "policy"}, None, "document_policy"),
    ({"work": "general"}, None, "general_question"),
    ({"work": "reasoning"}, None, "complex_reasoning"),
    ({"work": "cant_do"}, None, "account_action"),
    ({"work": "unclear"}, None, "ambiguous"),
])
def test_code_follows_jevs_picks_to_a_route(
        picks: dict[str, Any], said: Danger | None, expected: str) -> None:
    assert handler(decided(**picks), said).name == expected


def test_every_kind_of_work_has_a_route_in_dans_table() -> None:
    assert len(ROUTES) == 9
    assert {handler(decided(work=work)).name for work in WORK} | {
        "danger", "multi_part"} == set(ROUTES)


def decide_alone(question: str, answers: dict[str, Any]) -> Decisions:
    request = ChatRequest.model_validate({"messages": [user(question)]})
    context = read_context(request, datetime(2026, 9, 29, 12, 0, tzinfo=CAMPUS_TIMEZONE))
    return decide(context, checked(answers, QUESTIONS))


def test_options_that_lead_to_the_same_thing_count_together() -> None:
    # Run 1 (09-29): split between two answerable readings, so no one option was sure.
    decisions = decide_alone("What room is it in?", calm(
        needs=pick("campus_info", {**dict.fromkeys(NEEDS, 0.0), "campus_info": 0.54,
                                   "conversation": 0.4, "guess": 0.06})))
    assert decisions.needs == "campus_info" and decisions.sureness["needs"] == 0.54
    assert decisions.reach == "supported" and decisions.sureness["reach"] == pytest.approx(0.94)


def test_a_pick_jev_isnt_sure_of_is_followed_and_marked() -> None:
    answers = calm(work=sure_pick("policy", WORK, 0.6), multi_part=yes(0.3))
    chosen = handler(decide_alone("How do I get a parking permit?", answers))
    assert chosen.name == "document_policy"
    assert chosen.path == ("danger", "own_account", "multi_part", "work")
    assert chosen.low_confidence == {"multi_part": 0.7, "work": 0.6}


def test_an_unsure_pick_is_followed_not_turned_into_ambiguous() -> None:
    unsure = calm(work=sure_pick("look_up", WORK, 0.4))
    assert handler(decide_alone("Is the gym open?", unsure)).name == "campus_fact"
