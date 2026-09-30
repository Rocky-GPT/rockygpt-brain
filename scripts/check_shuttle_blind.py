"""Milestone 5's blind check: Jev reads the frozen blind shuttle questions in one of the
sets under evals/shuttle/ (blind, blind2 and blind3 are sets 1 to 3, spent on 09-29; blind4 is
set 4), and code scores what the shuttle questions made of them.

Each question is asked once, with the fixed Tuesday clock and the saved timetable the labels
were written for, in the one call of the nine frozen questions plus the shuttle ones, exactly
as a turn would ask them. Nothing else runs: no database, no answer text. Calls are paid by
Typesafe (about 2,500 input tokens, or $0.0001, each) and kept in a ledger that lives in
memory, so nothing is written to Neon.

    BRAIN_TYPESAFE_API_KEY=... python scripts/check_shuttle_blind.py --out /tmp/blind.json

The labels are checked against their frozen hash first, so a label edited after the freeze
stops the run. The bar was set before any run (docs of the review, 09-29):

- no wrong answers among the firm questions: a plan that differs from the label, or a plan
  for a question the label says is not ready
- at most 3 firm questions the label says to answer that were refused instead

The soft questions are scored and shown, but do not count toward the bar. This set is spent
once it has run: any change to the questions after seeing it needs a fresh set.
"""

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rockygpt_brain.campus import Timetable, parse
from rockygpt_brain.context import CAMPUS_TIMEZONE, Context
from rockygpt_brain.contract import ChatMessage
from rockygpt_brain.decisions import ask_jev, handler, readings
from rockygpt_brain.jev import Jev, JevError, TypesafeHttp
from rockygpt_brain.safety import said_danger
from rockygpt_brain.shuttle_ask import Asking, dispatch, shuttle_questions
from rockygpt_brain.spending import SpendingError

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "shuttle" / "blind4" / "cases.json"
TIMETABLE = ROOT / "tests" / "fixtures" / "shuttle-timetable-20260929.json"
MAX_LOST = 3
# Stop early if the run has cost this much: nano-dollars, so $0.10.
SPEND_LIMIT_NUSD = 100_000_000


class MemoryLedger:
    """Holds and settles in memory: the run is paid to Typesafe but never written to Neon."""

    def __init__(self) -> None:
        self.holds: dict[str, str] = {}

    def hold(self, request_id: str, category: Any, amount: int, metadata: dict[str, Any],
             now: datetime) -> str:
        operation = f"op{len(self.holds) + 1}"
        self.holds[operation] = "reserved"
        return operation

    def settle(self, operation: str, cost: int, usage: dict[str, int], response_id: str,
               model: str, elapsed_ms: int, now: datetime) -> None:
        self.holds[operation] = "settled"

    def uncertain(self, operation: str, code: str, elapsed_ms: int) -> None:
        self.holds[operation] = "uncertain"


def frozen_hash(cases: list[dict[str, Any]]) -> str:
    core = [{key: case[key] for key in ("id", "earlier", "question", "expect", "plan", "gate")}
            for case in cases]
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load(path: Path = CASES) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text())
    if frozen_hash(doc["cases"]) != doc["frozen_sha256"]:
        raise SystemExit("The blind labels no longer match their frozen hash.")
    return doc


def timetable() -> Timetable:
    rows = json.loads(TIMETABLE.read_text())["rows"]
    for row in rows:
        row["collected_at"] = datetime.fromisoformat(row["collected_at"])
    return parse(rows)


def context_of(case: dict[str, Any], now: datetime) -> Context:
    return Context(question=case["question"], omitted=0, now=now,
                   earlier=tuple(ChatMessage(role="user", content=text)
                                 for text in case["earlier"]))


def outcome(case: dict[str, Any], plan: Any) -> str:
    """right_answer, wrong_answer, lost_answer, right_refusal or false_answer."""
    if case["expect"] == "dispatch":
        if plan is None:
            return "lost_answer"
        expected = case["plan"]
        same = (plan.operation == expected["operation"] and plan.stop == expected["stop"]
                and plan.day == date.fromisoformat(expected["day"]))
        return "right_answer" if same else "wrong_answer"
    return "false_answer" if plan is not None else "right_refusal"


def score(case: dict[str, Any], asking: Asking, jev: Jev, now: datetime) -> dict[str, Any]:
    context = context_of(case, now)
    decisions, asked = ask_jev(jev, context, str(uuid4()), None, asking.questions)
    chosen = handler(decisions, said_danger(case["question"]))
    result = dispatch(asked.answers, asking, decisions, chosen, context)
    verdict = outcome(case, result.plan)
    return {
        "id": case["id"], "question": case["question"], "earlier": case["earlier"],
        "status": case["status"], "scored": case["scored"], "outcome": verdict,
        "expected": case["expect"], "expected_gate": case["gate"],
        "expected_plan": case["plan"],
        "got_plan": None if result.plan is None else {
            "operation": result.plan.operation, "day": result.plan.day.isoformat(),
            "stop": result.plan.stop},
        "refused": result.refused,
        "gate_matches": None if result.plan is not None or case["gate"] is None
        else result.refused == case["gate"],
        "handler": chosen.name, "picks": result.picks,
        "readings": {key: value for key, value in readings(asked.answers).items()
                     if key.startswith("shuttle_")},
        "cost_nusd": asked.cost_nusd, "elapsed_ms": asked.elapsed_ms,
    }


def bar(results: list[dict[str, Any]], skipped: list[dict[str, str]]) -> dict[str, Any]:
    firm = [result for result in results if result["scored"]]
    def count(kind: str) -> int:
        return sum(result["outcome"] == kind for result in firm)

    wrong = count("wrong_answer") + count("false_answer")
    lost = count("lost_answer")
    complete = not skipped
    return {
        "firm": len(firm), "right": count("right_answer") + count("right_refusal"),
        "wrong_or_false": wrong, "lost": lost, "skipped": len(skipped),
        "passed": complete and wrong == 0 and lost <= MAX_LOST,
        "complete": complete,
    }


def run(jev: Jev, doc: dict[str, Any], limit: int | None = None,
        say: Callable[[str], None] = print) -> dict[str, Any]:
    now = datetime.fromisoformat(doc["clock"]).astimezone(CAMPUS_TIMEZONE)
    asking = shuttle_questions(now, timetable())
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    spent = 0
    for number, case in enumerate(doc["cases"], 1):
        if limit is not None and number > limit:
            break
        try:
            result = score(case, asking, jev, now)
        except JevError as error:
            skipped.append({"id": case["id"], "code": error.code})
            say(f"{case['id']}: Jev skipped ({error.code})")
            continue
        except SpendingError as error:
            say(f"Stopped: the ledger refused ({error.code})")
            break
        results.append(result)
        spent += result["cost_nusd"]
        if spent > SPEND_LIMIT_NUSD:
            say("Stopped: the run cost more than $0.10.")
            break
    return {"asked_at": datetime.now(CAMPUS_TIMEZONE).isoformat(),
            "frozen_sha256": doc["frozen_sha256"], "cases": len(results), "skipped": skipped,
            "cost_usd": round(spent / 1e9, 6), "bar": bar(results, skipped),
            "results": results}


def misses(report: dict[str, Any]) -> list[str]:
    lines = []
    for result in report["results"]:
        if result["outcome"] in {"right_answer", "right_refusal"}:
            continue
        got = result["got_plan"] or f"refused at {result['refused']}"
        lines.append(f"{result['id']} {result['outcome']}"
                     f"{'' if result['scored'] else ' (soft, not scored)'}: expected "
                     f"{result['expected_plan'] or result['expected_gate']}, got {got}: "
                     f"{result['question']!r}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=Path, default=CASES, help="the blind set to ask")
    parser.add_argument("--limit", type=int, help="ask only the first N questions")
    parser.add_argument("--out", type=Path, help="write every result here as JSON")
    arguments = parser.parse_args(argv)
    key = os.getenv("BRAIN_TYPESAFE_API_KEY", "").strip()
    if not key:
        print("Jev needs BRAIN_TYPESAFE_API_KEY", file=sys.stderr)
        return 2
    report = run(Jev(TypesafeHttp(key), MemoryLedger()), load(arguments.cases),
                 arguments.limit)
    if arguments.out:
        arguments.out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    verdict = report["bar"]
    print(f"{report['cases']} questions, {len(report['skipped'])} skipped, "
          f"${report['cost_usd']}")
    print(f"firm {verdict['firm']}: {verdict['right']} right, {verdict['wrong_or_false']} wrong "
          f"or false, {verdict['lost']} lost. Bar {'PASSED' if verdict['passed'] else 'NOT MET'}")
    print("\n".join(misses(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
