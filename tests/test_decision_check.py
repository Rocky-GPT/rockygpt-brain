"""The labeled questions for milestone 4's live check, and the check itself run on a fake
Jev (scripts/check_decisions.py)."""

import importlib.util
import json
from pathlib import Path
from typing import Any

from fakes import ScriptedJev, calm, fake_jev
from rockygpt_brain.decisions import ASKS, DANGER, HANDLERS, NAMED, REACH, SUBJECTS

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_decisions",
                                              ROOT / "scripts" / "check_decisions.py")
assert spec and spec.loader
check: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
CASES = json.loads((ROOT / "evals" / "decisions" / "cases.json").read_text())


def test_every_label_is_an_answer_jev_or_code_can_give() -> None:
    options = {"asks": ASKS, "subject": SUBJECTS, "named": NAMED,
               "reach": set(REACH.values()), "danger": DANGER, "handler": HANDLERS}
    cases = [case for conversation in CASES["conversations"].values() for case in conversation]
    assert len(cases) == 130
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        labels = case["labels"]
        for item, allowed in options.items():
            assert labels[item] and set(labels[item]) <= set(allowed), (case["id"], item)
        assert labels["needs_earlier"] in {True, False, None}
        assert labels["multi_part"] in {True, False, None}


def test_the_first_question_of_a_conversation_has_no_earlier_messages() -> None:
    contexts = [context for _, context in check.conversations(CASES)]
    assert contexts[0].first_question
    assert [message.content for message in contexts[2].earlier] == [
        case["question"] for case in CASES["conversations"]["audit"][:2]]
    assert contexts[30].first_question


def test_the_check_scores_each_item_and_counts_the_cost() -> None:
    jev, _, _ = fake_jev(ScriptedJev(calm(), input_tokens=2000))
    report = check.run(jev, CASES, limit=13, say=lambda line: None)
    assert report["cases"] == 13 and report["cost_usd"] == 13 * 2000 * 42 / 1e9
    # The fake reads every question as "Where is the Registrar?", and the 13th is that.
    registrar = report["results"][12]
    assert registrar["question"] == "Where is the Registrar?"
    assert set(registrar["verdicts"].values()) <= {"right", None}
    assert registrar["decided"]["handler"] == "exact"
    shuttle = report["results"][0]["verdicts"]
    assert shuttle["subject"] == "wrong" and shuttle["asks"] == "right"
    assert report["summary"]["subject"]["scored"] == 13
    assert check.misses(report)
