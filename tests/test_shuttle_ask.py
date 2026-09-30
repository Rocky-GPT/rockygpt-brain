"""The shuttle questions Jev is asked (shuttle_ask.py), the options code writes for each turn,
and the gate that decides whether Jev's answers make a plan. Jev here is a script: nothing in
this file reaches Typesafe or spends."""

import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from fakes import ScriptedJev, calm, calm_shuttle, fake_jev, pick, sure_pick, yes
from rockygpt_brain.campus import Timetable, parse
from rockygpt_brain.context import CAMPUS_TIMEZONE, Context, read_context
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.decisions import QUESTIONS, Decisions, Handler, ask_jev, handler
from rockygpt_brain.jev import CALL_TOKENS, Answer, JevError, Yes, checked, token_bound
from rockygpt_brain.shuttle_answer import ShuttlePlan
from rockygpt_brain.shuttle_ask import (
    FIXED,
    FLOOR,
    Asking,
    Dispatch,
    day_options,
    dispatch,
    shuttle_questions,
    stop_options,
)

FIXTURE = Path(__file__).parent / "fixtures" / "shuttle-timetable-20260929.json"
NOW = datetime(2026, 9, 29, 13, 0, tzinfo=CAMPUS_TIMEZONE)  # a Tuesday
TUESDAY = date(2026, 9, 29)


def timetable() -> Timetable:
    rows = json.loads(FIXTURE.read_text())["rows"]
    for row in rows:
        row["collected_at"] = datetime.fromisoformat(row["collected_at"])
    return parse(rows)


TABLE = timetable()
ASKING = shuttle_questions(NOW, TABLE)
GSP = "garden state plaza"
TRIP, WANTS = (ASKING.questions[name]["criteria"] for name in ("shuttle_trip", "shuttle_wants"))
DAYS, STOPS = (ASKING.questions[name]["criteria"] for name in ("shuttle_day", "shuttle_stop"))


def context(*earlier: str, omitted: int = 0, now: datetime = NOW) -> Context:
    messages = [{"role": "user", "content": text} for text in earlier]
    messages.append({"role": "user", "content": "When is the next shuttle to GSP?"})
    request = {"messages": messages, "omittedMessages": omitted}
    return read_context(ChatRequest.model_validate(request), now)


def decisions(**changes: Any) -> Decisions:
    ordinary: dict[str, Any] = {
        "danger": None, "own_account": False, "needs_earlier": False, "work": "look_up",
        "subject": "transport", "named": "none", "needs": "campus_info", "multi_part": False,
        "reach": "supported"}
    return Decisions(**{**ordinary, **changes}, sureness=dict.fromkeys(ordinary, 0.96))


def route(name: str = "campus_fact") -> Handler:
    return Handler(name, ("danger", "own_account", "multi_part", "work"), {})


def go(wire: dict[str, Any] | None = None, *, answers: Mapping[str, Answer] | None = None,
       chosen: str = "campus_fact", ctx: Context | None = None, asking: Asking = ASKING,
       **changes: Any) -> Dispatch:
    """The gate on Jev's shuttle answers (calm ones unless `wire` says otherwise, as Typesafe
    sends them), with the Decisions fields in `changes`."""
    if answers is None:
        answers = checked(wire if wire is not None else calm_shuttle(asking), asking.questions)
    return dispatch(answers, asking, decisions(**changes), route(chosen), ctx or context())


def stop_id(key: str) -> str:
    return next(sid for sid, value in ASKING.stops.items() if value == key)


def test_the_nine_frozen_questions_are_untouched_and_the_shuttle_ones_are_added() -> None:
    assert len(QUESTIONS) == 9
    assert ASKING.questions.keys() == FIXED.keys() | {"shuttle_day", "shuttle_stop"}
    assert not ASKING.questions.keys() & QUESTIONS.keys()
    jev = ScriptedJev()
    jev.answers = {**calm(), **calm_shuttle(ASKING)}
    decided, asked = ask_jev(fake_jev(jev)[0], context(), "r1", None, ASKING.questions)
    (sent,) = jev.sent
    assert list(sent["questions"])[:9] == list(QUESTIONS) and len(sent["questions"]) == 15
    assert {name: sent["questions"][name] for name in QUESTIONS} == QUESTIONS
    assert decided.subject == "places" and "shuttle_day" in asked.answers


def test_with_no_extra_questions_the_call_is_exactly_what_it_was() -> None:
    jev = ScriptedJev()
    ask_jev(fake_jev(jev)[0], context(), "r1")
    assert jev.sent[0]["questions"] == QUESTIONS


def test_every_question_talks_about_the_students_words_and_names_no_brain_label() -> None:
    text = json.dumps(ASKING.questions)
    for question in ASKING.questions.values():
        assert "latest_request" in question["instructions"]
        assert "route" not in question["instructions"].lower()  # a Brain label, in the ask
    for label in ("executor", "handler", "entity", "database", "record", "GPT", "Jev"):
        assert label not in text


def test_no_pick_one_option_is_a_catch_all_but_the_ones_that_say_what_they_are() -> None:
    for name in ("shuttle_trip", "shuttle_wants", "shuttle_day", "shuttle_stop"):
        for said in ASKING.questions[name]["criteria"].values():
            assert "anything else" not in said and "any other" not in said


def test_a_reply_missing_or_adding_a_key_is_thrown_away_whole() -> None:
    every = {**QUESTIONS, **ASKING.questions}
    good = {**calm(), **calm_shuttle(ASKING)}
    assert checked(good, every).keys() == every.keys()
    missing = {k: v for k, v in good.items() if k != "shuttle_day"}
    for broken in (missing, {**good, "extra": yes(0.5)}):
        with pytest.raises(JevError):
            checked(broken, every)


def test_the_call_stays_small() -> None:
    added = len(json.dumps(ASKING.questions, separators=(",", ":")).encode())
    assert added < 5000  # measured at about 3.7 KB on 5.8 KB of frozen questions
    assert token_bound({"state": {}, "questions": {**QUESTIONS, **ASKING.questions}}) < CALL_TOKENS


def test_the_days_are_written_from_the_clock_and_each_weekday_appears_once() -> None:
    options, days = day_options(NOW)
    assert list(days) == [f"d{n}" for n in range(7)]
    assert days["d0"] == TUESDAY and days["d1"] == TUESDAY + timedelta(days=1)
    assert options["d0"].startswith("Today, Tuesday, September 29")
    assert "names Tuesday" in options["d0"]
    assert options["d1"] == "Tomorrow, Wednesday, September 30"
    assert options["d2"] == "Thursday, October 1, the day after tomorrow"
    assert options["d6"] == "Monday, October 5"
    weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    described = [said for key, said in options.items() if key.startswith("d")]
    assert all(sum(name in said for said in described) == 1 for name in weekdays)
    assert list(options)[-1:] == ["other"] and "none" not in options  # no day said is today
    assert "says no day at all" in options["d0"]


def test_the_days_follow_the_clock_across_a_week_a_month_and_a_year() -> None:
    sunday = datetime(2026, 10, 4, 23, 59, tzinfo=CAMPUS_TIMEZONE)
    options, days = day_options(sunday)
    assert options["d0"].startswith("Today, Sunday, October 4")
    assert options["d1"] == "Tomorrow, Monday, October 5" and days["d6"] == date(2026, 10, 10)
    new_year = datetime(2026, 12, 30, 9, 0, tzinfo=CAMPUS_TIMEZONE)
    options, days = day_options(new_year)
    assert options["d3"] == "Saturday, January 2" and days["d3"] == date(2027, 1, 2)


def test_the_stops_are_written_from_the_timetable_with_short_ids() -> None:
    options, stops = stop_options(TABLE)
    assert list(stops) == [f"s{n}" for n in range(1, 8)]
    assert [stops[f"s{n}"] for n in (1, 3)] == ["ramsey rt 17 train", "garden state plaza"]
    assert options["s3"] == "Going to or stopping at Garden State Plaza"
    assert options["s5"] == "Going to or stopping at City MD Ramsey"
    assert list(options)[-2:] == ["none", "other"]
    # A new place in the data is a new option, and nothing else moves.
    extra = TABLE.stop_menu + (type(TABLE.stop_menu[0])("newark penn", "Newark Penn", 1),)
    grown = Timetable(TABLE.dataset_version, TABLE.source, TABLE.routes, TABLE.collected_at, extra)
    new_options, new_stops = stop_options(grown)
    assert new_stops["s8"] == "newark penn" and new_options["s8"].endswith("Newark Penn")
    assert {k: v for k, v in new_options.items() if k in options} == options


def test_calm_answers_make_the_plan_a_careful_student_would_expect() -> None:
    result = go()
    assert result.refused is None
    assert result.plan == ShuttlePlan("next", TUESDAY, GSP)
    assert result.picks["day"] == "2026-09-29" and result.picks["stop"] == GSP
    assert result.picks["lowConfidence"] == {}


def test_no_day_means_today_and_no_stop_means_any_stop() -> None:
    answers = calm_shuttle(ASKING, shuttle_stop=sure_pick("none", STOPS))
    assert go(answers).plan == ShuttlePlan("next", TUESDAY, None)


def test_a_named_day_and_a_named_stop_come_through() -> None:
    answers = calm_shuttle(
        ASKING, shuttle_trip=sure_pick("last", TRIP), shuttle_day=sure_pick("d1", DAYS),
        shuttle_stop=sure_pick(stop_id("ramsey square"), STOPS))
    assert go(answers).plan == ShuttlePlan("last", TUESDAY + timedelta(days=1), "ramsey square")


def refuse_with(**changes: dict[str, Any]) -> Dispatch:
    return go(calm_shuttle(ASKING, **changes))




@pytest.mark.parametrize(("gate", "result"), [
    ("not_shuttle", lambda: refuse_with(shuttle_times=yes(0.1))),
    ("not_shuttle", lambda: refuse_with(shuttle_wants=sure_pick("stops", WANTS))),
    ("not_shuttle", lambda: refuse_with(shuttle_wants=sure_pick("elsewhere", WANTS))),
    ("clock", lambda: refuse_with(shuttle_clock=yes(0.9))),
    ("trip", lambda: refuse_with(shuttle_trip=sure_pick("all", TRIP))),
    ("day", lambda: refuse_with(shuttle_day=sure_pick("other", DAYS))),
    ("stop", lambda: refuse_with(shuttle_stop=sure_pick("other", STOPS))),
    ("route", lambda: go(chosen="document_policy")),
    ("route", lambda: go(chosen="general_question")),
    ("route", lambda: go(chosen="ambiguous")),
    ("route", lambda: go(chosen="danger")),
    ("follow_up", lambda: go(needs_earlier=True, ctx=context("What is the next shuttle?"))),
    ("not_asked", lambda: go(answers={})),
    ("not_asked", lambda: go(answers={"shuttle_times": Yes(0.9)})),
])
def test_every_gate_that_says_no_leaves_the_turn_not_ready(gate: str, result: Any) -> None:
    outcome = result()
    assert outcome.plan is None and outcome.refused == gate


def test_a_first_question_that_leans_on_nothing_is_not_a_follow_up() -> None:
    assert go(needs_earlier=True).plan is not None  # nothing came before it
    assert go(needs_earlier=False, ctx=context("What is the next shuttle?")).plan is not None


def test_the_first_gate_to_say_no_is_the_one_reported() -> None:
    answers = calm_shuttle(ASKING, shuttle_times=yes(0.1), shuttle_clock=yes(0.9))
    assert go(answers, chosen="document_policy").refused == "route"
    assert go(answers).refused == "not_shuttle"
    assert go(calm_shuttle(ASKING, shuttle_clock=yes(0.9))).refused == "clock"


def test_jevs_top_pick_is_followed_when_it_is_unsure_and_marked() -> None:
    unsure = calm_shuttle(ASKING, shuttle_trip=pick("first", {
        **dict.fromkeys(TRIP, 0.0), "first": 0.7, "next": 0.3}, 0.1))
    result = go(unsure)
    assert result.plan == ShuttlePlan("first", TUESDAY, GSP)
    assert result.picks["lowConfidence"] == {"trip": 0.7}


def spread(chosen: str, options: Any, probability: float) -> dict[str, Any]:
    """A pick-one answer with `chosen` at `probability` and the rest sharing what is left."""
    return pick(chosen, {key: probability if key == chosen else (1 - probability) / (
        len(options) - 1) for key in options})


@pytest.mark.parametrize("changes", [
    lambda: {"shuttle_times": yes(0.55)},
    lambda: {"shuttle_clock": yes(0.45)},
    lambda: {"shuttle_wants": spread("leaves", WANTS, 0.5)},
    lambda: {"shuttle_trip": spread("next", TRIP, 0.55)},
    lambda: {"shuttle_day": spread("d0", DAYS, 0.59)},
    lambda: {"shuttle_stop": spread(stop_id(GSP), STOPS, 0.5)},
])
def test_an_answer_is_never_built_on_a_pick_under_the_floor(changes: Any) -> None:
    result = go(calm_shuttle(ASKING, **changes()))
    assert result.plan is None and result.refused == "unsure"


def test_a_pick_at_the_floor_is_followed_and_a_gate_that_already_said_no_is_reported() -> None:
    assert FLOOR == 0.6
    at_floor = calm_shuttle(ASKING, shuttle_wants=spread("leaves", WANTS, FLOOR))
    assert go(at_floor).plan == ShuttlePlan("next", TUESDAY, GSP)
    # A coin flip on the way to a refusal is still the refusal that came first.
    coin = calm_shuttle(ASKING, shuttle_clock=yes(0.9), shuttle_wants=spread("leaves", WANTS, 0.5))
    assert go(coin).refused == "clock"
    assert go(calm_shuttle(ASKING, shuttle_wants=spread("stops", WANTS, 0.5))).refused \
        == "not_shuttle"


def test_what_goes_to_the_dev_metrics_holds_no_student_words() -> None:
    result = go(chosen="document_policy")
    shown = json.dumps(result.picks)
    # The student wrote "GSP" and "next shuttle"; only codes, a date and a place remain.
    assert "GSP" not in shown and "next shuttle" not in shown
    assert set(result.picks) == {"times", "wants", "trip", "clock", "day", "stop",
                                 "lowConfidence"}
    assert result.picks["stop"] == GSP and result.picks["day"] == "2026-09-29"


def test_a_calculate_route_and_a_campus_fact_route_both_reach_the_gate() -> None:
    assert go(chosen="exact").plan is not None
    assert go(chosen="campus_fact").plan is not None


def test_a_conversation_the_app_cut_is_not_a_first_question() -> None:
    cut = context(omitted=4)
    assert go(needs_earlier=True, ctx=cut).refused == "follow_up"
    assert go(needs_earlier=False, ctx=cut).plan is not None


def test_the_day_comes_from_the_clock_the_questions_were_written_for() -> None:
    late = datetime(2027, 1, 14, 23, 59, tzinfo=CAMPUS_TIMEZONE)  # not the day tests run on
    written = shuttle_questions(late, TABLE)
    days = written.questions["shuttle_day"]["criteria"]
    after_midnight = context(now=datetime(2027, 1, 15, 0, 1, tzinfo=CAMPUS_TIMEZONE))
    answers = calm_shuttle(written, shuttle_day=sure_pick("d0", days))
    result = go(answers, ctx=after_midnight, asking=written)
    assert result.plan is not None and result.plan.day == date(2027, 1, 14)


def test_each_pick_reports_the_gate_that_says_no_first() -> None:
    both = calm_shuttle(ASKING, shuttle_trip=sure_pick("all", TRIP),
                        shuttle_day=sure_pick("other", DAYS))
    assert go(both).refused == "trip"
    assert go(both, chosen="document_policy").refused == "route"
    clock = calm_shuttle(ASKING, shuttle_clock=yes(0.9))
    assert go(clock, needs_earlier=True, ctx=context("What is the next shuttle?")
              ).refused == "follow_up"
    assert go(calm_shuttle(ASKING, shuttle_day=sure_pick("other", DAYS),
                           shuttle_stop=sure_pick("other", STOPS))).refused == "day"


def test_a_reply_missing_one_shuttle_question_is_not_asked() -> None:
    answers = checked(calm_shuttle(ASKING), ASKING.questions)
    partial = {key: value for key, value in answers.items() if key != "shuttle_day"}
    assert go(answers=partial).refused == "not_asked"


def test_when_the_timetable_could_not_be_read_a_plain_shuttle_question_is_a_data_outage() -> None:
    blind = shuttle_questions(NOW, None)
    assert "shuttle_stop" not in blind.questions and blind.stops == {}
    outage = go(asking=blind)
    assert outage.refused == "data_unavailable" and outage.picks["stop"] is None
    # It is only an outage for a question code would otherwise have answered.
    assert go(calm_shuttle(blind, shuttle_clock=yes(0.9)), asking=blind).refused == "clock"
    assert go(calm_shuttle(blind, shuttle_times=yes(0.1)), asking=blind).refused == "not_shuttle"
    assert go(asking=blind, chosen="document_policy").refused == "route"


def test_a_coin_flip_is_not_an_outage_when_the_timetable_could_not_be_read() -> None:
    blind = shuttle_questions(NOW, None)
    days = blind.questions["shuttle_day"]["criteria"]
    shaky = calm_shuttle(blind, shuttle_day=spread("d0", days, 0.49))
    assert go(shaky, asking=blind).refused == "unsure"
    assert go(asking=blind).refused == "data_unavailable"  # a firm reading still is


def test_extra_questions_may_add_to_the_frozen_ones_never_replace_them() -> None:
    with pytest.raises(ValueError):
        ask_jev(fake_jev(ScriptedJev())[0], context(), "r1", None,
                {"subject": FIXED["shuttle_times"]})


def test_the_whole_path_from_a_scripted_jev_to_a_plan() -> None:
    jev = ScriptedJev()
    jev.answers = {**calm(subject=sure_pick("transport", {"transport": 0.96, **dict.fromkeys(
        ("dining", "places", "people", "academics", "student_life", "housing", "money", "safety",
         "none"), 0.005)}), work=sure_pick("look_up", {
             "calculate": 0.01, "look_up": 0.96, "policy": 0.01, "general": 0.005,
             "reasoning": 0.005, "cant_do": 0.005, "unclear": 0.005})),
                   **calm_shuttle(ASKING)}
    ctx = context()
    decided, asked = ask_jev(fake_jev(jev)[0], ctx, "r1", None, ASKING.questions)
    result = dispatch(asked.answers, ASKING, decided, handler(decided), ctx)
    assert result.plan == ShuttlePlan("next", TUESDAY, GSP)
