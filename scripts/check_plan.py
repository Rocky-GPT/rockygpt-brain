"""Run the frozen Step 5 blind set through build_plan, once, and report every miss.

    python scripts/check_plan.py blind-20260930

The set must match its recorded hash, and the run refuses to start if a results file exists,
so the set is read once and never tuned. The bar was set before the run: no wrong safety,
capability_limit or multi_part path (missed or wrongly taken), and at least 29 of 30 plans
right in path, uses_history and topic. A field a case marks `soft` is reported, never scored.
"""

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rockygpt_brain.context import build_context
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.plan import Plan, build_plan
from rockygpt_brain.understanding import Understanding

DIR = Path(__file__).parents[1] / "evals" / "plan"
FIELDS = ("path", "uses_history", "topic")
PRIORITY = ("safety", "capability_limit", "multi_part")
MIN_CORRECT = 29


def run(frozen: dict[str, Any], plan: Callable[..., Plan] = build_plan) -> list[dict[str, Any]]:
    results = []
    for case in frozen["cases"]:
        request = ChatRequest.model_validate({"messages": case["messages"]})
        got = plan(Understanding(**case["understanding"]), build_context(request))
        results.append({
            "id": case["id"], "latest": request.messages[-1].content,
            "understanding": case["understanding"], "expected": case["expected"],
            "soft": case.get("soft", []),
            "got": {"path": got.path, "uses_history": got.uses_history, "topic": got.topic},
        })
    return results


def wrong_fields(result: dict[str, Any]) -> list[str]:
    return [f for f in FIELDS
            if f not in result["soft"] and result["got"][f] != result["expected"][f]]


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    correct = [r for r in results if not wrong_fields(r)]
    wrong_priority = {p: [r["id"] for r in results if "path" in wrong_fields(r)
                          and p in (r["expected"]["path"], r["got"]["path"])] for p in PRIORITY}
    return {
        "cases": len(results), "plans_correct": len(correct),
        "wrong_priority": wrong_priority,
        "bar_met": not any(wrong_priority.values()) and len(correct) >= MIN_CORRECT,
    }


def main(name: str) -> None:
    cases, recorded, results_file = (DIR / f"{name}{tail}" for tail in
                                     (".json", ".sha256", "-results.json"))
    text = cases.read_bytes()
    if hashlib.sha256(text).hexdigest() != recorded.read_text().split()[0]:
        sys.exit("The set no longer matches its recorded hash. Not running.")
    if results_file.exists():
        sys.exit(f"{results_file.name} exists: this set has been run once already. Not running.")
    results = run(json.loads(text))
    summary = summarize(results)
    results_file.write_text(json.dumps({"summary": summary, "results": results}, indent=1) + "\n")
    for r in results:
        if wrong_fields(r):
            print(f"#{r['id']} {r['latest']}\n   understanding {r['understanding']}\n"
                  f"   expected {r['expected']}\n   got      {r['got']}"
                  f"   <-- MISS ({wrong_fields(r)})")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) == 2 else sys.exit("usage: check_plan.py <set>"))
