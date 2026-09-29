"""Milestone 4's live check: Jev reads every labeled question in evals/decisions/cases.json,
and code scores what it decided against the labels.

Each conversation is asked in order, one Jev call per question, with the conversation's
earlier questions as its earlier messages. Every call is paid through the development
ledger, like a turn: about 2,000 input tokens, or $0.0001, each.

    BRAIN_ENVIRONMENT=development BRAIN_LEDGER_DATABASE_URL=... BRAIN_TYPESAFE_API_KEY=... \\
        python scripts/check_decisions.py --out /tmp/decisions.json

An item is right when Jev was sure and its answer is one the label accepts, unsure when
Jev wasn't sure (the Brain then decides nothing), and wrong otherwise. `top` counts Jev's
top pick whether or not it was sure.
"""

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rockygpt_brain.context import CAMPUS_TIMEZONE, Context
from rockygpt_brain.contract import ChatMessage
from rockygpt_brain.decisions import REACH, Decisions, ask_jev, handler, readings
from rockygpt_brain.jev import Asked, Jev, JevError, Pick
from rockygpt_brain.safety import said_danger
from rockygpt_brain.spending import SpendingError
from rockygpt_brain.turn import worst

CASES = Path(__file__).resolve().parents[1] / "evals" / "decisions" / "cases.json"
# A Tuesday afternoon in term.
NOW = datetime(2026, 9, 29, 13, 0, tzinfo=CAMPUS_TIMEZONE)

# Dan's six items (09-29), each with the labels and answers it is scored on.
ITEMS = {
    "asks": "What it asks",
    "needs_earlier": "Needs history",
    "danger": "Dangerous (Jev)",
    "danger_or_phrases": "Dangerous (Jev or phrases)",
    "reach": "Private, live-only or unsupported",
    "subject": "Campus area",
    "named": "Kind of thing named",
    "handler": "Which handler",
    "multi_part": "Several separate asks",
}
PICKS = {"asks": "asks", "subject": "subject", "named": "named", "reach": "needs"}


def conversations(cases: dict[str, Any]) -> Iterator[tuple[dict[str, Any], Context]]:
    """Each case with its context: the conversation's questions before it."""
    for conversation in cases["conversations"].values():
        earlier: list[ChatMessage] = []
        for case in conversation:
            yield case, Context(question=case["question"], earlier=tuple(earlier), omitted=0,
                                now=NOW)
            earlier.append(ChatMessage(role="user", content=case["question"]))


def verdict(decided: Any, accepted: Any) -> str | None:
    """right, unsure or wrong; None when the label accepts either answer."""
    if accepted is None:
        return None
    if decided is None:
        return "unsure"
    right = decided in accepted if isinstance(accepted, list) else decided == accepted
    return "right" if right else "wrong"


def score(case: dict[str, Any], decisions: Decisions, asked: Asked) -> dict[str, Any]:
    labels = case["labels"]
    said = said_danger(case["question"])
    decided = {
        "asks": decisions.asks,
        "subject": decisions.subject,
        "named": decisions.named,
        "reach": decisions.reach,
        "needs_earlier": decisions.needs_earlier,
        "multi_part": decisions.multi_part,
        # A danger pick acts on Jev's top pick alone, so it is always decided.
        "danger": decisions.danger or "none",
        "danger_or_phrases": worst(said, decisions.danger) or "none",
        "handler": handler(decisions, said),
    }
    accepted = {**labels, "danger_or_phrases": labels["danger"]}
    verdicts = {item: verdict(decided[item], accepted[item]) for item in ITEMS}
    top = {}
    for item, key in PICKS.items():
        answer = asked.answers[key]
        assert isinstance(answer, Pick)
        choice = REACH[answer.choice] if item == "reach" else answer.choice
        top[item] = choice in labels[item]
    return {"id": case["id"], "question": case["question"], "decided": decided,
            "verdicts": verdicts, "top_right": top, "readings": readings(asked.answers),
            "cost_nusd": asked.cost_nusd, "elapsed_ms": asked.elapsed_ms}


def summary(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    table: dict[str, dict[str, Any]] = {}
    for item, name in ITEMS.items():
        counts = {"right": 0, "unsure": 0, "wrong": 0}
        for result in results:
            if (outcome := result["verdicts"][item]) is not None:
                counts[outcome] += 1
        scored = sum(counts.values())
        row: dict[str, Any] = {"name": name, **counts, "scored": scored,
                               "accuracy": round(counts["right"] / scored, 3) if scored else None}
        if item in PICKS:
            row["top_accuracy"] = round(
                sum(result["top_right"][item] for result in results) / len(results), 3)
        table[item] = row
    return table


def run(jev: Jev, cases: dict[str, Any], limit: int | None = None,
        say: Callable[[str], None] = print) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for number, (case, context) in enumerate(conversations(cases), 1):
        if limit is not None and number > limit:
            break
        try:
            decisions, asked = ask_jev(jev, context, str(uuid4()))
        except JevError as error:
            skipped.append({"id": case["id"], "code": error.code})
            say(f"{case['id']}: Jev skipped ({error.code})")
            continue
        except SpendingError as error:
            say(f"Stopped: the ledger refused ({error.code})")
            break
        results.append(score(case, decisions, asked))
    times = [result["elapsed_ms"] for result in results]
    return {
        "asked_at": datetime.now(CAMPUS_TIMEZONE).isoformat(),
        "cases": len(results),
        "skipped": skipped,
        "cost_usd": round(sum(result["cost_nusd"] for result in results) / 1e9, 6),
        "jev_ms": {"median": statistics.median(times) if times else None,
                   "max": max(times) if times else None},
        "summary": summary(results),
        "results": results,
    }


def misses(report: dict[str, Any]) -> list[str]:
    lines = []
    for result in report["results"]:
        for item, outcome in result["verdicts"].items():
            if outcome in {"wrong", "unsure"} and item != "danger_or_phrases":
                key = PICKS.get(item, item)
                reading = result["readings"].get(key)
                lines.append(f"{result['id']} {item} {outcome}: got {result['decided'][item]!r}"
                             f" ({reading}) for {result['question']!r}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, help="ask only the first N questions")
    parser.add_argument("--out", type=Path, help="write every result here as JSON")
    arguments = parser.parse_args(argv)
    from rockygpt_brain.api.app import environment, jev_service
    if environment() != "development":
        print("BRAIN_ENVIRONMENT must be development", file=sys.stderr)
        return 2
    jev = jev_service()
    if jev is None:
        print("Jev needs BRAIN_LEDGER_DATABASE_URL and BRAIN_TYPESAFE_API_KEY", file=sys.stderr)
        return 2
    report = run(jev, json.loads(CASES.read_text()), arguments.limit)
    if arguments.out:
        arguments.out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"{report['cases']} questions, {len(report['skipped'])} skipped, "
          f"${report['cost_usd']}, Jev median {report['jev_ms']['median']} ms")
    for row in report["summary"].values():
        top = f", top pick {row['top_accuracy']:.0%}" if "top_accuracy" in row else ""
        print(f"{row['name']}: {row['right']}/{row['scored']} right, {row['unsure']} unsure, "
              f"{row['wrong']} wrong{top}")
    print("\n".join(misses(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
