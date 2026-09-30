"""The Step 5 blind-set runner, tested with stand-in plan functions before its one real run."""

import hashlib
import json
from pathlib import Path
from typing import Any

import check_plan as runner
import pytest

from rockygpt_brain.context import Context
from rockygpt_brain.plan import Plan
from rockygpt_brain.understanding import Understanding

SET = "blind-20260930"
FROZEN = json.loads((runner.DIR / f"{SET}.json").read_text())


def expected_plan(understanding: Understanding, context: Context) -> Plan:
    case = next(c for c in FROZEN["cases"] if c["understanding"]["topic"] == understanding.topic
                and c["understanding"]["needs"] == understanding.needs
                and c["understanding"]["needs_history"] == understanding.needs_history
                and c["understanding"]["danger"] == understanding.danger
                and c["understanding"]["multi_part"] == understanding.multi_part
                and (len(c["messages"]) > 1) == context.history_available)
    want = case["expected"]
    return Plan(want["path"], want["topic"], want["uses_history"])


def test_every_frozen_set_matches_its_hash_and_has_thirty_cases() -> None:
    for hash_file in runner.DIR.glob("*.sha256"):
        cases = hash_file.with_suffix(".json")
        assert hashlib.sha256(cases.read_bytes()).hexdigest() == hash_file.read_text().split()[0]
    assert [c["id"] for c in FROZEN["cases"]] == list(range(1, 31))


def test_a_plan_function_that_agrees_with_the_key_meets_the_bar() -> None:
    summary = runner.summarize(runner.run(FROZEN, expected_plan))
    assert summary["plans_correct"] == 30 and summary["bar_met"]


def test_a_wrong_priority_path_fails_the_bar() -> None:
    def never_safe(understanding: Understanding, context: Context) -> Plan:
        plan = expected_plan(understanding, context)
        return Plan("campus" if plan.path == "safety" else plan.path, plan.topic, plan.uses_history)

    summary = runner.summarize(runner.run(FROZEN, never_safe))
    assert summary["wrong_priority"]["safety"] and not summary["bar_met"]


def test_the_bar_is_twenty_nine_of_thirty_and_a_soft_field_is_not_scored() -> None:
    def wrong_topic_on(count: int) -> Any:
        seen: list[int] = []

        def plan(understanding: Understanding, context: Context) -> Plan:
            good = expected_plan(understanding, context)
            seen.append(1)
            return Plan(good.path, "none" if len(seen) <= count and good.topic != "none"
                        else good.topic, good.uses_history)
        return plan

    assert runner.summarize(runner.run(FROZEN, wrong_topic_on(1)))["plans_correct"] >= 29
    frozen = json.loads(json.dumps(FROZEN))
    for case in frozen["cases"]:
        case["soft"] = ["topic"]
    assert runner.summarize(runner.run(frozen, wrong_topic_on(30)))["plans_correct"] == 30


def test_main_refuses_a_changed_set_and_a_second_run(tmp_path: Path, monkeypatch: Any) -> None:
    for tail in (".json", ".sha256"):
        (tmp_path / f"{SET}{tail}").write_bytes((runner.DIR / f"{SET}{tail}").read_bytes())
    monkeypatch.setattr(runner, "DIR", tmp_path)
    changed = (tmp_path / f"{SET}.json").read_bytes()
    (tmp_path / f"{SET}.json").write_bytes(changed + b" ")
    with pytest.raises(SystemExit, match="hash"):
        runner.main(SET)
    (tmp_path / f"{SET}.json").write_bytes(changed)
    (tmp_path / f"{SET}-results.json").write_text("{}")
    with pytest.raises(SystemExit, match="once already"):
        runner.main(SET)
