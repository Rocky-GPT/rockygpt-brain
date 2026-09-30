"""The blind-set runner, tested with a fake Jev so it can be trusted for its one real run."""

import hashlib
import json
from pathlib import Path
from typing import Any

import check_understanding as runner
import pytest

OLD = "blind-20260930"
FROZEN = json.loads((runner.DIR / f"{OLD}.json").read_text())


def jev_says(expected: dict[str, Any]) -> dict[str, Any]:
    def choice(value: str) -> dict[str, Any]:
        return {"type": "choice", "choice": value, "probabilities": {value: 0.9, "other": 0.1}}

    return {"answers": {
        "needs": choice(expected["needs"]), "topic": choice(expected["topic"]),
        **{f: {"type": "noul", "noul": 0.95 if expected.get(f) else 0.05}
           for f in ("needs_history", "history_resolves", "danger", "multi_part")},
    }}


def perfect(body: dict[str, Any]) -> dict[str, Any]:
    latest = body["state"]["latest_message"]
    case = next(c for c in FROZEN["cases"] if c["messages"][-1]["content"] == latest)
    return jev_says(case["expected"])


def test_every_frozen_set_matches_its_hash() -> None:
    recorded = sorted(runner.DIR.glob("*.sha256"))
    assert recorded
    for hash_file in recorded:
        cases = hash_file.with_suffix(".json")
        assert hashlib.sha256(cases.read_bytes()).hexdigest() == hash_file.read_text().split()[0]


def test_the_first_blind_set_has_forty_cases() -> None:
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


def test_a_soft_field_is_reported_but_never_scored() -> None:
    soft = {**FROZEN, "cases": [{**FROZEN["cases"][0], "soft": ["topic"]}]}

    def wrong_topic(body: dict[str, Any]) -> dict[str, Any]:
        reply = perfect(body)
        reply["answers"]["topic"]["choice"] = "none"
        return reply

    result = runner.run(soft, wrong_topic)
    assert runner.wrong_fields(result[0]) == []
    assert "(soft, not scored)" in runner.describe(result[0])
    assert runner.summarize(result)["fully_correct"] == 1

    strict = {**FROZEN, "cases": [FROZEN["cases"][0]]}
    assert runner.wrong_fields(runner.run(strict, wrong_topic)[0]) == ["topic"]


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


def test_the_pair_bar_is_thirty_seven_and_misses_are_counted_by_cell() -> None:
    def wrong_needs_on(count: int) -> Any:
        seen: list[str] = []

        def post(body: dict[str, Any]) -> dict[str, Any]:
            reply = perfect(body)
            seen.append("x")
            if len(seen) <= count:
                reply["answers"]["needs"]["choice"] = (
                    "outside" if reply["answers"]["needs"]["choice"] != "outside" else "unclear")
            return reply
        return post

    ok = runner.summarize(runner.run(FROZEN, wrong_needs_on(3)))
    assert ok["needs_and_needs_history_correct"] == 37 and ok["bar_met"]
    assert sum(ok["miss_clusters_diagnostic_only"].values()) == 3
    short = runner.summarize(runner.run(FROZEN, wrong_needs_on(4)))
    assert short["needs_and_needs_history_correct"] == 36 and not short["bar_met"]


def test_main_refuses_a_changed_set_and_a_second_run(
        tmp_path: Path, monkeypatch: Any) -> None:
    for tail in (".json", ".sha256"):
        (tmp_path / f"{OLD}{tail}").write_bytes((runner.DIR / f"{OLD}{tail}").read_bytes())
    monkeypatch.setattr(runner, "DIR", tmp_path)
    (tmp_path / f"{OLD}.json").write_bytes((tmp_path / f"{OLD}.json").read_bytes() + b" ")
    with pytest.raises(SystemExit, match="hash"):
        runner.main(OLD)
    (tmp_path / f"{OLD}.json").write_bytes((tmp_path / f"{OLD}.json").read_bytes()[:-1])
    (tmp_path / f"{OLD}-results.json").write_text("{}")
    with pytest.raises(SystemExit, match="once already"):
        runner.main(OLD)


HISTORY = json.loads((runner.DIR / "history-20260930.json").read_text())


def perfect_for(frozen: dict[str, Any]) -> Any:
    def post(body: dict[str, Any]) -> dict[str, Any]:
        state = body["state"]
        earlier = [m["content"] for m in state["recent_messages"]]
        for case in frozen["cases"]:
            if (case["messages"][-1]["content"] == state["latest_message"]
                    and [m["content"] for m in case["messages"][:-1]] == earlier):
                return jev_says(case["expected"])
        raise AssertionError("no such case")
    return post


def test_the_history_set_has_the_planned_shape() -> None:
    cases = HISTORY["cases"]
    assert [c["id"] for c in cases] == list(range(1, 41))
    cells = [(c["expected"]["needs_history"], c["expected"]["history_resolves"]) for c in cases]
    assert (cells.count((True, True)), cells.count((True, False)), cells.count((False, False))) == (
        16, 12, 12)
    assert all("history_resolves" in c["soft"] for c in cases if not c["expected"]["needs_history"])
    assert HISTORY["bar"] == {"fully_correct": 36,
                              "field_correct": {"needs_history": 37, "history_resolves": 24}}


def test_a_perfect_jev_meets_the_history_bar() -> None:
    summary = runner.summarize(runner.run(HISTORY, perfect_for(HISTORY)), HISTORY["bar"])
    assert summary["fully_correct"] == 40 and summary["bar_met"]
    assert summary["field_correct_of_scored"]["history_resolves"] == [26, 26]


def test_the_history_bar_needs_twenty_four_of_twenty_six_history_resolves() -> None:
    def wrong_resolves_on(count: int) -> Any:
        good = perfect_for(HISTORY)
        flipped: list[int] = []

        def post(body: dict[str, Any]) -> dict[str, Any]:
            reply: dict[str, Any] = good(body)
            answer = reply["answers"]["history_resolves"]
            if body["state"]["recent_messages"] and answer["noul"] > 0.5 and len(flipped) < count:
                flipped.append(1)
                answer["noul"] = 0.05
            return reply
        return post

    bar = HISTORY["bar"]
    assert runner.summarize(runner.run(HISTORY, wrong_resolves_on(2)), bar)["bar_met"]
    three = runner.summarize(runner.run(HISTORY, wrong_resolves_on(3)), bar)
    assert three["field_correct_of_scored"]["history_resolves"][0] == 23 and not three["bar_met"]


def test_soft_fields_and_clusters_never_fail_a_run() -> None:
    def wrong_on_soft(body: dict[str, Any]) -> dict[str, Any]:
        reply: dict[str, Any] = perfect_for(HISTORY)(body)
        if body["state"]["latest_message"].startswith("ok going back to that thing"):
            reply["answers"]["needs"]["choice"] = "campus_info"   # case 12: needs is soft
            reply["answers"]["history_resolves"]["noul"] = 0.95   # and so is history_resolves
        return reply

    summary = runner.summarize(runner.run(HISTORY, wrong_on_soft), HISTORY["bar"])
    assert summary["fully_correct"] == 40 and summary["bar_met"]
    assert summary["miss_clusters_diagnostic_only"] == {}


def test_the_regression_copies_keep_the_old_labels_but_the_amended_case_forty() -> None:
    blind1 = json.loads((runner.DIR / "regress-blind1-20260930.json").read_text())
    boundary = json.loads((runner.DIR / "regress-boundary-20260930.json").read_text())
    original = json.loads((runner.DIR / "blind-20260930.json").read_text())
    assert blind1["cases"][39]["expected"]["needs"] == "campus_info"
    assert [c for c in blind1["cases"][:39]] == original["cases"][:39]
    assert boundary["cases"] == json.loads(
        (runner.DIR / "boundary-20260930.json").read_text())["cases"]
    assert "bar" not in blind1 and "bar" not in boundary
