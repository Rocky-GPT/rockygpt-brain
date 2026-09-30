"""The blind shuttle questions (evals/shuttle/blind/cases.json) and the check that runs them
(scripts/check_shuttle_blind.py). Jev here is a script: nothing in this file reaches
Typesafe or spends. The real run is a separate step that needs Dan's yes."""

import importlib.util
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from fakes import calm, sure_pick, yes
from rockygpt_brain.context import CAMPUS_TIMEZONE
from rockygpt_brain.jev import JEV_PRICE, Jev, Reply
from rockygpt_brain.shuttle_answer import ShuttlePlan
from rockygpt_brain.shuttle_ask import shuttle_questions

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_shuttle_blind",
                                              ROOT / "scripts" / "check_shuttle_blind.py")
assert spec and spec.loader
check: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
# Set 1 was spent on 09-29 on the five-question design, set 2 on the six-question one, and
# set 3 is for the same six after one change to the "leaves" option.
SETS = {"blind": (ROOT / "evals" / "shuttle" / "blind" / "cases.json", 57),
        "blind2": (ROOT / "evals" / "shuttle" / "blind2" / "cases.json", 59),
        "blind3": (ROOT / "evals" / "shuttle" / "blind3" / "cases.json", 59),
        "blind4": (ROOT / "evals" / "shuttle" / "blind4" / "cases.json", 58)}
EACH = pytest.mark.parametrize("name", list(SETS))
DOCS = {name: check.load(path) for name, (path, _) in SETS.items()}
NOW = datetime.fromisoformat(DOCS["blind"]["clock"]).astimezone(CAMPUS_TIMEZONE)
TODAY = NOW.date()


@EACH
def test_the_labels_are_the_ones_that_were_frozen(name: str) -> None:
    doc, cases = DOCS[name], DOCS[name]["cases"]
    assert check.frozen_hash(cases) == doc["frozen_sha256"]
    assert len(cases) == 60 and len({case["id"] for case in cases}) == 60
    assert len({case["question"].strip().lower() for case in cases}) == 60
    assert sum(case["scored"] for case in cases) == SETS[name][1]
    assert all(case["scored"] == (case["status"] == "firm") for case in cases)


def test_a_label_changed_after_the_freeze_stops_the_run(tmp_path: Path) -> None:
    changed = json.loads(json.dumps(DOCS["blind2"]))
    changed["cases"][0]["expect"] = "not_ready" if changed["cases"][0]["expect"] == "dispatch" \
        else "dispatch"
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(changed))
    with pytest.raises(SystemExit):
        check.load(path)


@EACH
def test_none_of_them_is_a_practice_or_frozen_question_or_in_the_other_set(name: str) -> None:
    practice = json.loads((ROOT / "evals" / "shuttle" / "cases.json").read_text())["cases"]
    frozen = json.loads((ROOT / "evals" / "decisions" / "cases.json").read_text())
    known = {case["question"].strip().lower() for case in practice}
    known |= {case["question"].strip().lower()
              for group in frozen["conversations"].values() for case in group}
    for key, other in DOCS.items():
        if key != name:
            known |= {case["question"].strip().lower() for case in other["cases"]}
    assert not known & {case["question"].strip().lower() for case in DOCS[name]["cases"]}


@EACH
def test_every_label_is_something_the_questions_and_gates_can_say(name: str) -> None:
    asking = shuttle_questions(NOW, check.timetable())
    stops = set(asking.stops.values())
    for case in DOCS[name]["cases"]:
        if case["expect"] == "dispatch":
            plan = case["plan"]
            assert plan["operation"] in {"next", "first", "last"}, case["id"]
            assert 0 <= (date.fromisoformat(plan["day"]) - TODAY).days < 7, case["id"]
            assert plan["stop"] is None or plan["stop"] in stops, case["id"]
            assert case["gate"] is None, case["id"]
        else:
            assert case["plan"] is None
            assert case["gate"] in {"route", "follow_up", "not_shuttle", "clock", "trip", "day",
                                    "stop"}, case["id"]
            if case["gate"] == "follow_up":
                assert case["earlier"], case["id"]


def oracle(doc: dict[str, Any], input_tokens: int = 2500) -> Jev:
    """A Jev that answers each question the way its label says, sure of everything. A question
    that is not about a departure is answered as "not a shuttle question"."""
    by_question = {case["question"]: case for case in doc["cases"]}
    asking = shuttle_questions(NOW, check.timetable())
    stop_id = {key: sid for sid, key in asking.stops.items()}

    def send(body: dict[str, Any], timeout: float) -> Reply:
        case = by_question[body["state"]["latest_request"]]
        gate, plan = case["gate"], case["plan"]
        asked = body["questions"]
        day = "other" if gate == "day" else f"d{(date.fromisoformat(plan['day']) - TODAY).days}" \
            if plan else "d0"
        stop = "other" if gate == "stop" else stop_id[plan["stop"]] if plan and plan["stop"] \
            else "none"
        trip = "all" if gate == "trip" else plan["operation"] if plan else "next"
        answers = {
            **calm(multi_part=yes(0.97 if gate == "route" else 0.02),
                   needs_earlier=yes(0.97 if gate == "follow_up" else 0.03)),
            "shuttle_times": yes(0.03 if gate == "not_shuttle" else 0.97),
            "shuttle_wants": sure_pick("leaves", asked["shuttle_wants"]["criteria"]),
            "shuttle_clock": yes(0.97 if gate == "clock" else 0.03),
            "shuttle_trip": sure_pick(trip, asked["shuttle_trip"]["criteria"]),
            "shuttle_day": sure_pick(day, asked["shuttle_day"]["criteria"]),
            "shuttle_stop": sure_pick(stop, asked["shuttle_stop"]["criteria"]),
        }
        return Reply(answers, input_tokens, 100, JEV_PRICE.model, "resp")

    return Jev(send, check.MemoryLedger())


@EACH
def test_the_questions_can_hold_every_label_and_the_gates_read_them_back(name: str) -> None:
    """A Jev that understands every question perfectly gets all 60 right. That is the check
    that the form has a place for what each student meant, not a check of Jev."""
    doc = DOCS[name]
    report = check.run(oracle(doc), doc, say=lambda line: None)
    assert report["cases"] == 60 and not report["skipped"]
    wrong = [result["id"] for result in report["results"]
             if result["outcome"] not in {"right_answer", "right_refusal"}]
    assert wrong == []
    assert all(result["gate_matches"] in {None, True} for result in report["results"])
    assert report["bar"]["passed"] and report["bar"]["right"] == SETS[name][1]


@pytest.mark.parametrize(("expect", "plan", "kind"), [
    ("dispatch", ShuttlePlan("next", TODAY, "garden state plaza"), "right_answer"),
    ("dispatch", ShuttlePlan("last", TODAY, "garden state plaza"), "wrong_answer"),
    ("dispatch", ShuttlePlan("next", TODAY, None), "wrong_answer"),
    ("dispatch", None, "lost_answer"),
    ("not_ready", None, "right_refusal"),
    ("not_ready", ShuttlePlan("next", TODAY, None), "false_answer"),
])
def test_an_outcome_is_named_for_what_was_expected_and_what_came_back(
        expect: str, plan: Any, kind: str) -> None:
    case = {"expect": expect, "plan": {"operation": "next", "day": TODAY.isoformat(),
                                       "stop": "garden state plaza"}
            if expect == "dispatch" else None}
    assert check.outcome(case, plan) == kind


def rows(**counts: int) -> list[dict[str, Any]]:
    return [{"scored": True, "outcome": kind} for kind, number in counts.items()
            for _ in range(number)]


def test_the_bar_is_no_wrong_answers_and_at_most_three_lost() -> None:
    assert check.bar(rows(right_answer=20, right_refusal=34, lost_answer=3), [])["passed"]
    assert not check.bar(rows(right_answer=20, right_refusal=33, lost_answer=4), [])["passed"]
    assert not check.bar(rows(right_answer=23, right_refusal=33, wrong_answer=1), [])["passed"]
    assert not check.bar(rows(right_answer=24, right_refusal=32, false_answer=1), [])["passed"]
    skipped = [{"id": "blind-001", "code": "routing_timeout"}]
    assert not check.bar(rows(right_answer=24, right_refusal=32), skipped)["passed"]


def test_soft_questions_are_shown_but_do_not_count_toward_the_bar() -> None:
    mixed = rows(right_answer=24, right_refusal=32) + [
        {"scored": False, "outcome": "wrong_answer"}]
    verdict = check.bar(mixed, [])
    assert verdict["passed"] and verdict["firm"] == 56


def test_the_run_stops_when_it_has_cost_more_than_ten_cents() -> None:
    lines: list[str] = []
    doc = DOCS["blind2"]
    report = check.run(oracle(doc, input_tokens=10_000_000), doc, say=lines.append)  # $0.42 a call
    assert report["cases"] < 60 and any("more than $0.10" in line for line in lines)
