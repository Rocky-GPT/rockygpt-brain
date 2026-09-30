"""The shuttle in a whole turn (turn.py): Jev's shuttle answers become a plan, the plan is
answered from the timetable in code, and every way it can fail ends the way the design says.
Jev and the campus data are scripts here: nothing reaches Typesafe or a database."""

import dataclasses
import json
import logging
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fakes import ScriptedJev, calm, calm_shuttle, fake_jev, sure_pick, yes
from rockygpt_brain.api.app import app, campus_service, jev_service, log_turn
from rockygpt_brain.campus import CampusReader, CampusUnavailable, Timetable, parse
from rockygpt_brain.context import CAMPUS_TIMEZONE, Context
from rockygpt_brain.contract import ChatMessage, ChatReply, FailureReply, ProgressEvent
from rockygpt_brain.decisions import QUESTIONS
from rockygpt_brain.jev import JevError
from rockygpt_brain.shuttle_ask import shuttle_questions
from rockygpt_brain.turn import TurnResult, run_turn
from rockygpt_brain.work import Work

FIXTURE = Path(__file__).parent / "fixtures" / "shuttle-timetable-20260929.json"
NOW = datetime(2026, 9, 29, 13, 0, tzinfo=CAMPUS_TIMEZONE)  # a Tuesday
QUESTION = "When is the next shuttle to GSP?"


def timetable() -> Timetable:
    rows = json.loads(FIXTURE.read_text())["rows"]
    for row in rows:
        row["collected_at"] = datetime.fromisoformat(row["collected_at"])
    return parse(rows)


TABLE = timetable()
ASKING = shuttle_questions(NOW, TABLE)


class Reads:
    """A reader that counts how often the database is asked."""

    def __init__(self, error: str | None = None, table: Timetable = TABLE) -> None:
        self.error, self.count, self.table = error, 0, table

    def __call__(self) -> Timetable:
        self.count += 1
        if self.error:
            raise CampusUnavailable(self.error)
        return self.table


def turn(jev: ScriptedJev | None, reads: Reads | None, question: str = QUESTION,
         earlier: tuple[Any, ...] = (), omitted: int = 0, now: datetime = NOW,
         reader: CampusReader | None = None) -> tuple[list[ProgressEvent], TurnResult]:
    context = Context(question=question, earlier=earlier, omitted=omitted, now=now)
    service = fake_jev(jev)[0] if jev is not None else None
    campus = reader or (CampusReader(reads) if reads is not None else None)
    steps = list(run_turn(context, "req-1", service, Work(monotonic()), campus))
    events = [step for step in steps if isinstance(step, ProgressEvent)]
    result = steps[-1]
    assert isinstance(result, TurnResult)
    return events, result


def shuttle_jev(asking: Any = ASKING, **changes: dict[str, Any]) -> ScriptedJev:
    return ScriptedJev(answers={**calm(), **calm_shuttle(asking, **changes)})


def test_a_shuttle_question_is_answered_from_the_timetable_in_code() -> None:
    jev, reads = shuttle_jev(), Reads()
    events, result = turn(jev, reads)
    body = result.body
    assert isinstance(body, ChatReply) and result.status == 200 and body.status == "answered"
    assert body.answer.startswith("The published timetable lists the next shuttle from Ramapo")
    assert "Garden State Plaza" in body.answer and "Copied from Ramapo's published" in body.answer
    # Worked out from the turn's clock: the first Weekday Roadrunner trip after 1:00 PM.
    assert "after 1:00 PM" in body.answer and "leaves at 2:05 PM" in body.answer
    assert body.datasetVersion == TABLE.dataset_version
    assert body.citations
    assert all((c.collection or "").startswith("shuttle_") for c in body.citations)
    assert len(jev.sent) == 1 and len(jev.sent[0]["questions"]) == len(QUESTIONS) + 6
    assert reads.count == 1
    assert [event.stage for event in events] == ["understanding", "retrieving"]
    assert events[1].subjects[0].topic == "shuttle"  # the name the apps know
    assert events[1].subjects[0].date_from == "2026-09-29"
    metrics = result.metrics
    assert metrics["responseMode"] == "shuttle_timetable" and metrics["routingCalls"] == 1
    assert metrics["shuttle"]["kind"] == "found" and metrics["shuttle"]["refused"] is None
    assert metrics["shuttle"]["picks"]["stop"] == "garden state plaza"
    assert metrics["campus"]["datasetVersion"] == TABLE.dataset_version
    assert isinstance(metrics["campus"]["readMs"], int)
    assert "GSP" not in json.dumps(metrics)  # codes, dates and places: no student words


def test_the_dev_metrics_show_jevs_shuttle_answers_beside_the_nine() -> None:
    _, result = turn(shuttle_jev(), Reads())
    answers = result.metrics["jev"]["answers"]
    assert {"shuttle_times", "shuttle_wants", "shuttle_trip", "shuttle_clock", "shuttle_day",
            "shuttle_stop"} <= answers.keys()
    assert "subject" in answers and answers["shuttle_stop"]["choice"] == "s3"


def test_without_a_campus_setting_the_turn_is_what_it_was() -> None:
    jev = ScriptedJev(answers=calm())
    events, result = turn(jev, None)
    assert jev.sent[0]["questions"] == QUESTIONS
    assert isinstance(result.body, FailureReply) and result.body.reason == "not_ready"
    assert result.metrics["campus"] == {"skipped": "campus_not_configured"}
    assert "shuttle" not in result.metrics and len(events) == 1


def test_a_failed_read_ends_a_plain_shuttle_question_as_a_retryable_data_outage() -> None:
    blind = shuttle_questions(NOW, None)
    reads = Reads("campus_unreachable")
    jev = shuttle_jev(blind)
    _, result = turn(jev, reads)
    assert result.status == 503 and isinstance(result.body, FailureReply)
    assert result.body.reason == "data_unavailable" and result.body.error.retryable
    assert result.body.error.emergency is not None
    assert result.metrics["campus"]["skipped"] == "campus_unreachable"
    assert result.metrics["shuttle"]["refused"] == "data_unavailable"
    assert result.metrics["responseMode"] == "data_unavailable"
    assert "shuttle_stop" not in jev.sent[0]["questions"]


def test_a_failed_read_leaves_every_other_question_as_it_was() -> None:
    blind = shuttle_questions(NOW, None)
    not_shuttle = shuttle_jev(blind, shuttle_times=yes(0.03))
    _, result = turn(not_shuttle, Reads("campus_timeout"), "Where is the Registrar?")
    assert isinstance(result.body, FailureReply) and result.body.reason == "not_ready"
    assert result.metrics["campus"]["skipped"] == "campus_timeout"


def test_a_danger_phrase_is_answered_first_and_the_campus_data_is_never_read() -> None:
    reads, jev = Reads(), ScriptedJev(answers=calm())
    events, result = turn(jev, reads, "i want to kill myself")
    assert reads.count == 0 and result.safety is not None
    assert events[0].safety is not None
    assert result.metrics["campus"] == {"skipped": "danger_phrase"}
    assert result.metrics["responseMode"] == "safety_net"
    # Jev is still asked the nine questions, and only those, and answers them.
    assert jev.sent[0]["questions"] == QUESTIONS
    assert "answers" in result.metrics["jev"] and "shuttle" not in result.metrics


def test_a_danger_pick_beats_a_shuttle_answer() -> None:
    jev = shuttle_jev()
    jev.answers["danger"] = sure_pick("danger", {"self_harm": 0.01, "danger": 0.97, "none": 0.02})
    _, result = turn(jev, Reads(), "someone is following me, when is the next shuttle")
    assert result.safety is not None and result.metrics["responseMode"] == "safety_net"
    assert result.metrics["shuttle"]["refused"] == "route"


@pytest.mark.parametrize(("changes", "gate"), [
    ({"shuttle_clock": yes(0.9)}, "clock"),
    ({"shuttle_times": yes(0.1)}, "not_shuttle"),
    ({"shuttle_trip": sure_pick("all", ASKING.questions["shuttle_trip"]["criteria"])}, "trip"),
    ({"shuttle_day": sure_pick("other", ASKING.questions["shuttle_day"]["criteria"])}, "day"),
    ({"shuttle_stop": sure_pick("other", ASKING.questions["shuttle_stop"]["criteria"])}, "stop"),
])
def test_a_gate_that_says_no_leaves_the_turn_not_ready_and_the_metrics_say_which(
        changes: dict[str, Any], gate: str) -> None:
    _, result = turn(shuttle_jev(**changes), Reads())
    assert isinstance(result.body, FailureReply) and result.body.reason == "not_ready"
    assert result.metrics["shuttle"]["refused"] == gate
    assert result.metrics["responseMode"] == "not_ready"


def test_a_pick_on_a_coin_flip_is_not_answered() -> None:
    wants = ASKING.questions["shuttle_wants"]["criteria"]
    flip = {key: 0.5 if key == "leaves" else 0.25 for key in wants}
    jev = shuttle_jev(shuttle_wants={"type": "choice", "choice": "leaves", "probabilities": flip,
                                     "confidence": 0.1})
    _, result = turn(jev, Reads())
    assert isinstance(result.body, FailureReply) and result.body.reason == "not_ready"
    assert result.metrics["shuttle"]["refused"] == "unsure"


def test_a_stop_the_day_does_not_serve_is_a_sentence_from_code() -> None:
    farmers = sure_pick("s7", ASKING.questions["shuttle_stop"]["criteria"])
    jev = shuttle_jev(shuttle_stop=farmers)
    _, result = turn(jev, Reads())
    assert isinstance(result.body, ChatReply) and result.body.status == "answered"
    assert result.metrics["shuttle"]["kind"] == "stop_not_served"
    assert "lists no shuttle from Ramapo with a stop at Ramsey Farmers Market" in result.body.answer
    assert result.metrics["routingCalls"] == 1 and len(jev.sent) == 1


def test_a_day_with_nothing_left_is_a_sentence_from_code() -> None:
    late = datetime(2026, 9, 29, 23, 30, tzinfo=CAMPUS_TIMEZONE)
    asking = shuttle_questions(late, TABLE)
    _, result = turn(shuttle_jev(asking), Reads(), now=late)
    assert isinstance(result.body, ChatReply) and result.body.status == "answered"
    assert result.metrics["shuttle"]["kind"] == "none_left"
    assert "no more shuttles" in result.body.answer and "after 11:30 PM" in result.body.answer


def test_a_day_with_no_service_is_a_sentence_from_code() -> None:
    no_sunday = dataclasses.replace(
        TABLE, routes=tuple(r for r in TABLE.routes if r.service_day != "sunday"))
    sunday = sure_pick("d5", ASKING.questions["shuttle_day"]["criteria"])
    _, result = turn(shuttle_jev(shuttle_day=sunday), Reads(table=no_sunday))
    assert isinstance(result.body, ChatReply) and result.body.status == "answered"
    assert result.metrics["shuttle"]["kind"] == "no_service"
    assert "lists no shuttle from Ramapo on Sunday, Oct 4" in result.body.answer


def test_the_answer_moves_with_the_turns_clock() -> None:
    three = datetime(2026, 9, 29, 15, 0, tzinfo=CAMPUS_TIMEZONE)
    _, result = turn(shuttle_jev(shuttle_questions(three, TABLE)), Reads(), now=three)
    assert isinstance(result.body, ChatReply) and "after 3:00 PM" in result.body.answer
    assert "leaves at 2:05 PM" not in result.body.answer


def test_a_jev_failure_is_still_a_retryable_failure_and_no_shuttle_is_guessed() -> None:
    jev = ScriptedJev(error=JevError("routing_timeout"))
    _, result = turn(jev, Reads())
    assert isinstance(result.body, FailureReply) and result.body.reason == "model_timeout"
    assert "shuttle" not in result.metrics


def test_a_stale_copy_says_so() -> None:
    late = datetime(2026, 10, 20, 13, 0, tzinfo=CAMPUS_TIMEZONE)
    context = Context(question=QUESTION, earlier=(), omitted=0, now=late)
    asking = shuttle_questions(late, TABLE)
    jev = fake_jev(shuttle_jev(asking))[0]
    steps = list(run_turn(context, "req-1", jev, Work(monotonic()), CampusReader(Reads())))
    result = steps[-1]
    assert isinstance(result, TurnResult) and isinstance(result.body, ChatReply)
    assert "may be outdated" in result.body.answer and result.metrics["shuttle"]["stale"] is True


def test_a_follow_up_is_not_answered_from_the_timetable_alone() -> None:
    first = (ChatMessage(role="user", content="first shuttle to gsp tomorrow"),)
    leans = ScriptedJev(answers={**calm(needs_earlier=yes(0.85)), **calm_shuttle(ASKING)})
    _, result = turn(leans, Reads(), "and the last one?", earlier=first)
    assert isinstance(result.body, FailureReply) and result.body.reason == "not_ready"
    assert result.metrics["shuttle"]["refused"] == "follow_up"
    # The same words on their own are a question that stands alone, and are answered.
    _, alone = turn(shuttle_jev(), Reads(), earlier=first)
    assert isinstance(alone.body, ChatReply) and alone.body.status == "answered"
    # A chat the app cut short is not a first question either.
    _, cut = turn(leans, Reads(), omitted=4)
    assert isinstance(cut.body, FailureReply) and cut.metrics["shuttle"]["refused"] == "follow_up"


def test_a_two_part_question_is_not_answered_with_the_timetable_alone() -> None:
    two = ScriptedJev(answers={**calm(multi_part=yes(0.9)), **calm_shuttle(ASKING)})
    _, result = turn(two, Reads(), "next shuttle to gsp and where is the registrar")
    assert isinstance(result.body, FailureReply) and result.body.reason == "not_ready"
    assert result.metrics["handler"] == "multi_part"
    assert result.metrics["shuttle"]["refused"] == "route"


def test_an_account_request_gets_the_account_limit_not_a_timetable_answer() -> None:
    account = ScriptedJev(answers={**calm(own_account=yes(0.95), own_account_only=yes(0.95)),
                                   **calm_shuttle(ASKING)})
    _, result = turn(account, Reads(), "cancel my shuttle pass and tell me the next shuttle")
    assert isinstance(result.body, ChatReply) and result.body.status == "unavailable"
    assert result.metrics["responseMode"] == "access_limit"
    assert result.metrics["shuttle"]["refused"] == "route"


def test_two_turns_on_one_reader_read_the_database_once() -> None:
    reads = Reads()
    reader = CampusReader(reads)
    turn(shuttle_jev(), None, reader=reader)
    _, second = turn(shuttle_jev(), None, reader=reader)
    assert reads.count == 1 and isinstance(second.body, ChatReply)


def test_after_a_failed_read_the_next_turn_does_not_ask_the_database_at_once() -> None:
    reads = Reads("campus_unreachable")
    reader = CampusReader(reads)
    blind = shuttle_questions(NOW, None)
    _, first = turn(shuttle_jev(blind), None, reader=reader)
    _, second = turn(shuttle_jev(blind), None, reader=reader)
    assert reads.count == 1  # the 30 second pause, shared by the turns behind it
    assert first.metrics["campus"]["skipped"] == second.metrics["campus"]["skipped"]
    assert isinstance(second.body, FailureReply) and second.body.reason == "data_unavailable"


def test_with_jev_off_the_campus_data_is_not_read() -> None:
    reads = Reads()
    _, result = turn(None, reads)
    assert reads.count == 0
    assert result.metrics["campus"] == {"skipped": "routing_unavailable"}
    assert isinstance(result.body, FailureReply) and result.body.reason == "not_ready"


def test_a_bug_in_the_reader_is_an_internal_error_and_never_a_guess() -> None:
    def broken() -> Timetable:
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        turn(shuttle_jev(), None, reader=CampusReader(broken))
    app.dependency_overrides[jev_service] = lambda: fake_jev(shuttle_jev())[0]
    app.dependency_overrides[campus_service] = lambda: CampusReader(broken)
    reply = TestClient(app).post(
        "/v1/chat", json={"messages": [{"role": "user", "content": QUESTION}]})
    assert reply.status_code == 500 and reply.json()["reason"] == "internal_error"
    assert reply.json()["error"]["emergency"] is not None


client = TestClient(app)


def serve(jev: ScriptedJev, reader: Reads) -> None:
    app.dependency_overrides[jev_service] = lambda: fake_jev(jev)[0]
    app.dependency_overrides[campus_service] = lambda: CampusReader(reader)


def test_over_http_a_shuttle_question_is_answered_and_the_stream_shows_the_lookup() -> None:
    serve(shuttle_jev(), Reads())
    request = {"messages": [{"role": "user", "content": QUESTION}]}
    reply = client.post("/v1/chat", json=request)
    assert reply.status_code == 200 and reply.json()["status"] == "answered"
    assert reply.json()["answer"].startswith("The published timetable lists")
    streamed = client.post("/v1/chat", json=request, headers={"accept": "text/event-stream"})
    stages = [json.loads(line[6:])["stage"] for line in streamed.text.splitlines()
              if line.startswith("data: ") and '"stage"' in line]
    assert stages == ["connecting", "understanding", "retrieving"]
    assert "event: result" in streamed.text


def test_over_http_a_data_outage_is_a_503_the_app_can_retry() -> None:
    serve(shuttle_jev(shuttle_questions(NOW, None)), Reads("campus_unreachable"))
    reply = client.post("/v1/chat", json={"messages": [{"role": "user", "content": QUESTION}]})
    assert reply.status_code == 503
    assert reply.json()["reason"] == "data_unavailable" and reply.json()["error"]["retryable"]


def test_over_http_a_failed_read_streams_no_lookup_and_ends_in_the_outage() -> None:
    serve(shuttle_jev(shuttle_questions(NOW, None)), Reads("campus_unreachable"))
    request = {"messages": [{"role": "user", "content": QUESTION}]}
    streamed = client.post("/v1/chat", json=request, headers={"accept": "text/event-stream"})
    stages = [json.loads(line[6:])["stage"] for line in streamed.text.splitlines()
              if line.startswith("data: ") and '"stage"' in line]
    assert stages == ["connecting", "understanding"]
    result = [json.loads(line[6:]) for line in streamed.text.splitlines()
              if line.startswith("data: ") and '"status"' in line and '"body"' in line][-1]
    assert result["status"] == 503 and result["body"]["reason"] == "data_unavailable"


def test_over_http_without_a_campus_setting_a_shuttle_question_is_not_ready(
) -> None:
    app.dependency_overrides[jev_service] = lambda: fake_jev(ScriptedJev(answers=calm()))[0]
    app.dependency_overrides[campus_service] = lambda: None
    reply = client.post("/v1/chat", json={"messages": [{"role": "user", "content": QUESTION}]})
    assert reply.status_code == 503 and reply.json()["reason"] == "not_ready"


def test_the_dev_ui_sees_the_shuttle_step_and_who_wrote_the_answer(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    serve(shuttle_jev(), Reads())
    reply = client.post("/v1/chat", json={"messages": [{"role": "user", "content": QUESTION}]},
                        headers={"x-rockygpt-diagnostics": "1"})
    body = reply.json()
    steps = body["diagnostics"]["work"]["steps"]
    assert steps[-1]["stage"] == "retrieving"
    assert steps[-1]["written"] == {"by": "code", "mode": "shuttle_timetable"}
    assert body["metrics"]["campus"]["datasetVersion"] == TABLE.dataset_version
    assert len(body["diagnostics"]["work"]["calls"]) == 1  # Jev's one call, and no other


def test_the_campus_data_is_set_up_only_with_its_setting(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAIN_CAMPUS_DATABASE_URL", raising=False)
    campus_service.cache_clear()
    assert campus_service() is None
    monkeypatch.setenv("BRAIN_CAMPUS_DATABASE_URL", "   ")
    campus_service.cache_clear()
    assert campus_service() is None
    monkeypatch.setenv("BRAIN_CAMPUS_DATABASE_URL", "postgresql://user:secret@127.0.0.1:1/x")
    campus_service.cache_clear()
    reader = campus_service()
    assert isinstance(reader, CampusReader) and "secret" not in repr(reader)
    campus_service.cache_clear()


def test_the_turn_log_says_what_happened_to_the_shuttle_and_holds_no_words(
        caplog: pytest.LogCaptureFixture) -> None:
    blind = shuttle_questions(NOW, None)
    _, outage = turn(shuttle_jev(blind), Reads("campus_setup"))
    _, refused = turn(shuttle_jev(shuttle_clock=yes(0.9)), Reads())
    _, answered = turn(shuttle_jev(), Reads())
    lines = []
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        for result in (outage, refused, answered):
            log_turn("req-1", result, monotonic())
        lines = [json.loads(record.getMessage().removeprefix("brain_turn "))
                 for record in caplog.records]
    assert [(line["campusSkipped"], line["shuttleRefused"], line["shuttleKind"])
            for line in lines] == [("campus_setup", "data_unavailable", None),
                                   (None, "clock", None), (None, None, "found")]
    assert "GSP" not in json.dumps(lines)
