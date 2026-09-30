"""The new labeled shuttle questions (evals/shuttle/cases.json) agree with the gate.

Each case has the labels a careful person would give and what slice 1 should do. This builds
Jev's answers from the labels alone, sure of every one, and runs the gate on them: if a label and
the gate disagree, the label or the gate is wrong. Nothing here asks Jev, and the labels are
not tuned to it (they wait for Dan's review before any paid run)."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from fakes import calm_shuttle, sure_pick, yes
from rockygpt_brain.campus import Timetable, parse
from rockygpt_brain.context import CAMPUS_TIMEZONE, read_context
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.decisions import Decisions, Handler
from rockygpt_brain.jev import checked
from rockygpt_brain.shuttle_answer import ShuttlePlan
from rockygpt_brain.shuttle_ask import dispatch, shuttle_questions

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "evals" / "shuttle" / "cases.json").read_text())
NOW = datetime.fromisoformat(CASES["clock"]).astimezone(CAMPUS_TIMEZONE)
DAY_KEY = {"today": "d0", "tomorrow": "d1", "wednesday": "d1", "thursday": "d2", "friday": "d3",
           "saturday": "d4", "sunday": "d5", "monday": "d6", "none": "d0", "several": "other",
           "outside": "other"}  # no day said is today; a group or a far day is no one of the 7


def timetable() -> Timetable:
    rows = json.loads((Path(__file__).parent / "fixtures"
                       / "shuttle-timetable-20260929.json").read_text())["rows"]
    for row in rows:
        row["collected_at"] = datetime.fromisoformat(row["collected_at"])
    return parse(rows)


ASKING = shuttle_questions(NOW, timetable())
STOP_ID = {key: sid for sid, key in ASKING.stops.items()}
cases: list[dict[str, Any]] = CASES["cases"]


def test_there_are_99_distinct_cases_with_a_reason_for_each() -> None:
    assert len(cases) == 99 and len({case["id"] for case in cases}) == 99
    assert len({case["question"] for case in cases}) == 99
    assert all(case["why"] and case["expect"] in {"dispatch", "not_ready"} for case in cases)
    assert sum(case["expect"] == "dispatch" for case in cases) == 37


def test_none_of_them_is_one_of_the_frozen_130() -> None:
    frozen = json.loads((ROOT / "evals" / "decisions" / "cases.json").read_text())
    asked = {case["question"].strip().lower()
             for group in frozen["conversations"].values() for case in group}
    assert not asked & {case["question"].strip().lower() for case in cases}


def test_every_label_is_something_the_questions_can_say() -> None:
    for case in cases:
        labels = case["labels"]
        if labels["shuttle"]:
            assert labels["trip"] in {"next", "first", "last", "earlier", "all"}, case["id"]
            assert labels["wants"] in {"leaves", "stops"}, case["id"]
            assert labels["trip"] != "earlier" or labels["needs_earlier"], case["id"]
            assert labels["day"] in DAY_KEY, case["id"]
            assert labels["stop"] in {*STOP_ID, "none", "several", "other"}, case["id"]
        else:
            assert labels["trip"] is None and labels["stop"] is None, case["id"]


def wire_answers(labels: dict[str, Any]) -> dict[str, Any]:
    # What it wants to know is one pick: a departure from Ramapo, the stops, or a ride from
    # somewhere else.
    wants = "elsewhere" if labels["back"] else labels["wants"] or "leaves"
    # A trip pointed to from earlier is a follow-up, which is refused before the trip is read.
    trip = "next" if labels["trip"] in {None, "earlier"} else labels["trip"]
    stop = "other" if labels["stop"] == "several" else labels["stop"] or "none"
    return calm_shuttle(
        ASKING,
        shuttle_times=yes(0.97 if labels["shuttle"] else 0.03),
        shuttle_wants=sure_pick(wants, ASKING.questions["shuttle_wants"]["criteria"]),
        shuttle_clock=yes(0.97 if labels["clock"] else 0.03),
        shuttle_trip=sure_pick(trip, ASKING.questions["shuttle_trip"]["criteria"]),
        shuttle_day=sure_pick(DAY_KEY[labels["day"] or "none"],
                              ASKING.questions["shuttle_day"]["criteria"]),
        shuttle_stop=sure_pick(STOP_ID.get(stop, stop),
                               ASKING.questions["shuttle_stop"]["criteria"]))


@pytest.mark.parametrize("case", cases, ids=[case["id"] for case in cases])
def test_the_gate_does_with_the_labels_what_the_case_says_it_should(case: dict[str, Any]) -> None:
    labels = case["labels"]
    messages = [{"role": "user", "content": text} for text in case["earlier"]]
    messages.append({"role": "user", "content": case["question"]})
    context = read_context(ChatRequest.model_validate({"messages": messages}), NOW)
    decisions = Decisions(
        danger=None, own_account=False, needs_earlier=labels["needs_earlier"], work="look_up",
        subject="transport", named="none",
        needs="campus_info", multi_part=False, reach="supported", sureness={})
    answers = checked(wire_answers(labels), ASKING.questions)
    chosen = Handler(case.get("route", "campus_fact"), (), {})  # 097 and 098 go to another route
    result = dispatch(answers, ASKING, decisions, chosen, context)
    assert (result.plan is not None) == (case["expect"] == "dispatch"), (case["id"], result.refused)
    if case["plan"] is not None:
        expected = case["plan"]
        assert result.plan == ShuttlePlan(
            expected["operation"], date.fromisoformat(expected["day"]), expected["stop"])
