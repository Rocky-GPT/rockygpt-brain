"""Run the fixed capability-classifier evaluation exactly once per invocation."""

import argparse
import json
import os
from pathlib import Path
from typing import cast

from rockygpt_brain.capabilities import (
    CAPABILITY_LABELS,
    CapabilityLabel,
    ConversationMessage,
    classify,
)

DEFAULT_DATASET = Path(__file__).parents[1] / "evals" / "capability_classifier.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", default=os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"))
    arguments = parser.parse_args()

    cases = json.loads(arguments.dataset.read_text(encoding="utf-8"))
    failures: list[dict[str, object]] = []
    for case in cases:
        expected = tuple(cast(list[CapabilityLabel], case["expected"]))
        invalid = [label for label in expected if label not in CAPABILITY_LABELS]
        if invalid:
            raise ValueError(f"Unknown expected labels in {case['name']}: {invalid}")
        messages = cast(list[ConversationMessage], case["messages"])
        actual, _ = classify(messages, arguments.model)
        if actual != expected:
            failures.append(
                {"name": case["name"], "expected": expected, "actual": actual}
            )

    total = len(cases)
    correct = total - len(failures)
    report = {
        "model": arguments.model,
        "dataset": str(arguments.dataset),
        "correct": correct,
        "total": total,
        "accuracy": correct / total if total else 0,
        "failures": failures,
    }
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
