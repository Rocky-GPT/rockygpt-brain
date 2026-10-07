"""The ambiguity evaluation's runner and its frozen cases, tested offline (no Brain, no model)."""

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

EVAL = Path(__file__).resolve().parents[1] / "evals" / "ambiguity"
spec = importlib.util.spec_from_file_location("ambiguity_run", EVAL / "run.py")
assert spec and spec.loader
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def packet(**parts: object) -> dict[str, Any]:
    base = {"facts": [], "ambiguities": [], "notices": [], "missing": [], "unresolved": [],
            "status": "x"}
    return {"facts": {**base, **parts}}


FACT = {"subject": {"name": "Registrar"}, "predicate": "email"}


def test_the_cases_are_frozen() -> None:
    raw = (EVAL / "cases.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (EVAL / "cases.sha256").read_text().strip()


def test_the_cases_are_well_formed() -> None:
    cases = json.loads((EVAL / "cases.json").read_text())
    assert len({c["id"] for c in cases}) == len(cases) == 72
    for case in cases:
        assert case["expected"] in {"ask", "answer", "soft"} and case["question"].strip()
        assert all(m["role"] in {"user", "assistant"} and m["content"] for m in case["history"])
        assert ("office" in case) == (case["expected"] == "answer")
        assert ("disputed" in case) <= (case["expected"] == "soft")
    scored = [c for c in cases if c["expected"] != "soft"]
    assert (len(scored), sum(c["expected"] == "ask" for c in scored)) == (56, 26)


def test_what_the_brain_did_is_read_from_its_packet() -> None:
    choices = packet(ambiguities=[{"candidates": [{"name": "A"}, {"name": "B"}]}])
    assert run.observe(choices)["detail"] == "choices: A, B"
    assert run.observe(packet(notices=[{"type": "clarification"}]))["observed"] == "asked"
    extra = {**FACT, "subject": {"name": "Bursar"}, "purpose": "x"}
    seen = run.observe(packet(facts=[FACT, extra]))
    assert seen == {"observed": "answered", "detail": "Registrar", "offices": ["Registrar"]}
    missing = packet(status="not_found", unresolved=[{"query": "q"}])
    assert run.observe(missing)["observed"] == "other"
    absent = packet(not_published=[{"subject": {"name": "Nursing"}, "predicate": "email"}])
    assert run.observe(absent) == {
        "observed": "answered", "detail": "Nursing", "offices": ["Nursing"]}
    emergency_only = packet(not_published=[{"subject": {"name": "Public Safety"}, "purpose": "x"}])
    assert run.observe(emergency_only)["observed"] == "other"
    assert run.observe({"error": {"code": "provider_unavailable"}})["observed"] == "other"
    # A packet that holds facts and also asks is a question: the Brain did not just answer.
    both = packet(facts=[FACT], notices=[{"type": "clarification"}])
    assert run.observe(both)["observed"] == "asked"


def test_each_case_is_judged_by_its_own_label_and_soft_cases_are_never_scored() -> None:
    asked = {"observed": "asked", "offices": []}
    answered = {"observed": "answered", "offices": ["Registrar"]}
    assert run.judge({"expected": "ask"}, asked) == "ok"
    assert run.judge({"expected": "ask"}, answered) == "MISS"
    assert run.judge({"expected": "answer", "office": "Registrar"}, answered) == "ok"
    assert run.judge({"expected": "answer", "office": "Financial Aid"}, answered) == "WRONG OFFICE"
    assert run.judge({"expected": "answer", "office": "Registrar"}, asked) == "MISS"
    assert run.judge({"expected": "soft"}, answered) == "soft"
    rows = [{"expected": "ask", "verdict": "ok"}, {"expected": "ask", "verdict": "MISS"},
            {"expected": "answer", "verdict": "ok"}, {"expected": "soft", "verdict": "soft"}]
    assert run.summarize(rows) == {"scored": 3, "ok": 2, "ask": {"scored": 2, "ok": 1},
                                   "answer": {"scored": 1, "ok": 1}}
