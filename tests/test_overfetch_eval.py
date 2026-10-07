"""The field-selection evaluation's runner and frozen cases, tested offline (no Brain, no model)."""

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

EVAL = Path(__file__).resolve().parents[1] / "evals" / "overfetch"
spec = importlib.util.spec_from_file_location("overfetch_run", EVAL / "run.py")
assert spec and spec.loader
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

FIELDS = {"email", "phones", "offices", "hours", "website", "name", "department",
          "preferred_contact", "contact_note", "prefers_email"}


def packet(fields: list[str], entities: int = 1) -> dict[str, Any]:
    return {"facts": {"status": "complete",
                      "request": {"fields": fields, "entities": [{}] * entities}}}


def case(needs: list[str], generic: bool = False, scored: bool = True) -> dict[str, Any]:
    return {"needs": needs, "generic": generic, "scored": scored}


def test_the_cases_are_frozen() -> None:
    raw = (EVAL / "cases.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (EVAL / "cases.sha256").read_text().strip()


def test_the_cases_are_well_formed() -> None:
    cases = json.loads((EVAL / "cases.json").read_text())
    assert len({c["id"] for c in cases}) == len(cases) == 72
    for item in cases:
        assert item["question"].strip() and item["needs"] and set(item["needs"]) <= FIELDS
        assert all(m["role"] in {"user", "assistant"} and m["content"] for m in item["history"])
        assert ("disputed" in item) == (not item["scored"])
        assert "&amp;" not in item["office"]
    assert sum(c["scored"] for c in cases) == 69
    assert sum(c["generic"] and c["scored"] for c in cases) == 9


def test_what_the_brain_fetched_is_read_from_its_packet() -> None:
    assert run.observe(packet(["phones", "email", "email"]))["fields"] == ["email", "phones"]
    assert run.observe(packet(["email"], entities=0))["looked_up"] is False
    assert run.observe(packet([], entities=1))["looked_up"] is False
    assert run.observe({"error": {"code": "provider_unavailable"}})["looked_up"] is False


def seen(fields: list[str], looked_up: bool = True) -> dict[str, Any]:
    return {"fields": fields, "looked_up": looked_up}


def test_each_case_is_judged_against_what_the_student_asked_for() -> None:
    assert run.judge(case(["email"]), seen(["email"])) == "ok"
    assert run.judge(case(["email"]), seen(["email", "phones", "offices"])) == "OVER"
    assert run.judge(case(["email", "phones"]), seen(["email"])) == "UNDER"
    assert run.judge(case(["email"]), seen(["phones", "offices"])) == "BOTH"
    assert run.judge(case(["hours"]), seen([], looked_up=False)) == "NO LOOKUP"
    assert run.judge(case(["hours"], scored=False), seen(["email"])) == "soft"


def test_a_general_contact_request_may_also_fetch_the_room_and_nothing_else() -> None:
    generic = case(["email", "phones"], generic=True)
    assert run.judge(generic, seen(["email", "phones"])) == "ok"
    assert run.judge(generic, seen(["email", "phones", "offices"])) == "ok"
    assert run.judge(generic, seen(["email", "phones", "offices", "hours"])) == "OVER"
    assert run.judge(generic, seen(["email", "offices"])) == "UNDER"
    # The room is not allowed for a specific request.
    assert run.judge(case(["email", "phones"]), seen(["email", "phones", "offices"])) == "OVER"


def row(verdict: str, need: list[str], got: list[str]) -> dict[str, Any]:
    return {"verdict": verdict, "needs": need, "fields": got, "looked_up": verdict != "NO LOOKUP"}


def test_the_summary_and_the_pass_bar() -> None:
    rows = [row("ok", ["email"], ["email"])] * 9 + [row("OVER", ["email"], ["email", "phones"]),
                                                    {"verdict": "soft", "needs": [], "fields": []}]
    summary = run.summarize(rows)
    assert summary == {"scored": 10, "ok": 9, "OVER": 1, "UNDER": 0, "BOTH": 0, "NO LOOKUP": 0,
                       "under": 0, "extra_fields": 1}
    assert run.bar(summary, None) == []
    worse = {**summary, "ok": 8, "under": 2}
    assert len(run.bar(worse, None)) == 2
    baseline = {**summary, "under": 0, "NO LOOKUP": 0}
    assert run.bar({**summary, "under": 1}, baseline) != []
    assert run.bar({**summary, "NO LOOKUP": 1}, baseline) != []
