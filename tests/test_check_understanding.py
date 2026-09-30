"""The blind-set runner, tested with a fake Jev so it can be trusted for its one real run."""

import hashlib
import json
from pathlib import Path
from typing import Any

import check_understanding as runner
import pytest

FROZEN = json.loads(runner.CASES.read_text())


def jev_says(expected: dict[str, Any]) -> dict[str, Any]:
    def choice(value: str) -> dict[str, Any]:
        return {"type": "choice", "choice": value, "probabilities": {value: 0.9, "other": 0.1}}

    return {"answers": {
        "needs": choice(expected["needs"]), "topic": choice(expected["topic"]),
        **{f: {"type": "noul", "noul": 0.95 if expected[f] else 0.05}
           for f in ("needs_history", "danger", "multi_part")},
    }}


def perfect(body: dict[str, Any]) -> dict[str, Any]:
    latest = body["state"]["latest_message"]
    case = next(c for c in FROZEN["cases"] if c["messages"][-1]["content"] == latest)
    return jev_says(case["expected"])


def test_the_frozen_set_matches_its_hash_and_has_forty_cases() -> None:
    assert hashlib.sha256(runner.CASES.read_bytes()).hexdigest() == (
        runner.HASH.read_text().split()[0])
    assert [c["id"] for c in FROZEN["cases"]] == list(range(1, 41))


def test_a_perfect_jev_meets_the_bar_with_one_call_per_case() -> None:
    calls: list[str] = []

    def post(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(body["state"]["latest_message"])
        return perfect(body)

    summary = runner.summarize(runner.run(FROZEN, post))
    assert len(calls) == 40
    assert summary["fully_correct"] == 40 and summary["bar_met"]


def test_a_missed_danger_or_a_let_through_account_request_fails_the_bar() -> None:
    def blind_to_danger(body: dict[str, Any]) -> dict[str, Any]:
        reply = perfect(body)
        reply["answers"]["danger"]["noul"] = 0.01
        return reply

    danger = [c["id"] for c in FROZEN["cases"] if c["expected"]["danger"]]
    summary = runner.summarize(runner.run(FROZEN, blind_to_danger))
    assert summary["missed_danger"] == danger and not summary["bar_met"]

    def lets_accounts_through(body: dict[str, Any]) -> dict[str, Any]:
        reply = perfect(body)
        if reply["answers"]["needs"]["choice"] == "own_account":
            reply["answers"]["needs"]["choice"] = "campus_info"
        return reply

    summary = runner.summarize(runner.run(FROZEN, lets_accounts_through))
    assert len(summary["own_account_let_through"]) == 8 and not summary["bar_met"]


def test_the_bar_is_thirty_six_full_understandings() -> None:
    def wrong_topic_on(count: int) -> Any:
        seen: list[str] = []

        def post(body: dict[str, Any]) -> dict[str, Any]:
            reply = perfect(body)
            seen.append("x")
            if len(seen) <= count:
                reply["answers"]["topic"]["choice"] = (
                    "dining" if reply["answers"]["topic"]["choice"] != "dining" else "housing")
            return reply
        return post

    assert runner.summarize(runner.run(FROZEN, wrong_topic_on(4)))["bar_met"]
    assert not runner.summarize(runner.run(FROZEN, wrong_topic_on(5)))["bar_met"]


def test_a_failed_call_is_a_recorded_miss_and_is_not_retried() -> None:
    calls: list[int] = []

    def post(body: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        raise TimeoutError("slow")

    results = runner.run(FROZEN, post)
    assert len(calls) == 40 and all(r["error"] and r["got"] is None for r in results)
    assert "ERROR" in runner.describe(results[0])


def test_a_miss_shows_expected_jevs_answer_and_probabilities() -> None:
    def wrong_needs(body: dict[str, Any]) -> dict[str, Any]:
        reply = perfect(body)
        reply["answers"]["needs"] = {"type": "choice", "choice": "outside",
                                     "probabilities": {"outside": 0.6, "campus_info": 0.4}}
        return reply

    results = runner.run(FROZEN, wrong_needs)
    result = next(r for r in results if r["expected"]["needs"] != "outside")
    text = runner.describe(result)
    assert f"expected {result['expected']['needs']}, Jev outside" in text
    assert "[outside 0.600, campus_info 0.400]" in text
    assert "<-- MISS" in text


def test_main_refuses_a_changed_set_and_a_second_run(
        tmp_path: Path, monkeypatch: Any) -> None:
    frozen = runner.CASES
    cases = tmp_path / "cases.json"
    cases.write_bytes(frozen.read_bytes() + b" ")
    monkeypatch.setattr(runner, "CASES", cases)
    with pytest.raises(SystemExit, match="hash"):
        runner.main()
    monkeypatch.setattr(runner, "CASES", frozen)
    results = tmp_path / "results.json"
    results.write_text("{}")
    monkeypatch.setattr(runner, "RESULTS", results)
    with pytest.raises(SystemExit, match="once already"):
        runner.main()
