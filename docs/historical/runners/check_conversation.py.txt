"""Ask the running dev Brain a scripted conversation, with the history you wrote.

The dev UI carries only answered turns into a conversation's history. While route handlers
are missing, "not ready" is a 503 with no answer, so its follow-ups ("what about
tomorrow?") arrive alone and Jev rightly can't tell what they mean. This sends each user
message in a file with every earlier message in that file exactly as written, and never
feeds a reply back, so the history is the one you meant. The Brain is not changed.

    python scripts/check_conversation.py evals/conversations/mixed-follow-ups.json
    python scripts/check_conversation.py FILE --brain http://127.0.0.1:8000 --out /tmp/run.json

The file is {"messages": [{"role": "user" | "assistant", "content": "..."}, ...]}. It
starts with a user message. Every user message is one turn: one Jev call, about $0.0001
through the development ledger. The Brain must run with BRAIN_ENVIRONMENT=development, or it
sends no metrics. Each line shows the HTTP status, the route code chose, how the turn ended,
the picks Jev put under 0.90, and the start of the answer.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

# What the Brain accepts in one request (contract.py ChatRequest): 1 to 80 messages.
MAX_MESSAGES = 80
Message = dict[str, str]
# Sends one /v1/chat body with the diagnostics header; returns the HTTP status and JSON body.
Post = Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]
DIAGNOSTICS = {"x-rockygpt-diagnostics": "1"}


def load(path: Path) -> list[Message]:
    """The conversation in a file, checked so a typo fails here and not as a 422 per turn."""
    messages = json.loads(path.read_text())["messages"]
    if not messages or messages[0].get("role") != "user":
        raise SystemExit(f"{path}: the conversation must start with a user message")
    for number, message in enumerate(messages, 1):
        if message.get("role") not in {"user", "assistant"} or not str(
                message.get("content", "")).strip():
            raise SystemExit(f"{path}: message {number} needs a role (user or assistant) "
                             "and some text")
    if len(messages) > MAX_MESSAGES:
        raise SystemExit(f"{path}: {len(messages)} messages; the Brain takes {MAX_MESSAGES}")
    return [{"role": m["role"], "content": m["content"]} for m in messages]


def turn(post: Post, sent: list[Message]) -> dict[str, Any]:
    """One turn: the last message is the question, the ones before it are its history."""
    status, body = post({"messages": sent})
    metrics = body.get("metrics") or {}
    jev = metrics.get("jev") or {}
    decided = jev.get("decided") or {}
    return {
        "question": sent[-1]["content"],
        "earlierMessages": len(sent) - 1,
        "httpStatus": status,
        "handler": metrics.get("handler"),
        "responseMode": metrics.get("responseMode"),
        "lowConfidence": decided.get("lowConfidence") or {},
        "jevSkipped": jev.get("skipped"),
        "costNusd": jev.get("costNusd") or 0,
        "answer": body.get("answer"),
        "reason": body.get("reason"),
        "hasMetrics": "metrics" in body,
    }


def run(messages: list[Message], post: Post) -> list[dict[str, Any]]:
    """Every user message, asked with all the messages before it in the file."""
    return [turn(post, messages[:index + 1])
            for index, message in enumerate(messages) if message["role"] == "user"]


def show(number: int, row: dict[str, Any]) -> str:
    low = ", ".join(f"{name} {round(value * 100)}%" for name, value in row["lowConfidence"].items())
    ended = row["responseMode"] or row["jevSkipped"] or "-"
    said = " ".join((row["answer"] or f"[{row['reason']}]").split())
    return (f"{number:>2}  {row['httpStatus']}  +{row['earlierMessages']:<2} "
            f"{row['handler'] or '-':<17} {ended:<14} {low or '-':<26} "
            f"{row['question'][:44]!r} -> {said[:64]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file", type=Path, help="the conversation, as JSON")
    parser.add_argument("--brain", default="http://127.0.0.1:8000", help="the dev Brain's URL")
    parser.add_argument("--out", type=Path, help="write every result here as JSON")
    args = parser.parse_args(argv)

    messages = load(args.file)
    # No proxy from the environment: this is a local Brain.
    with httpx.Client(base_url=args.brain, timeout=30, trust_env=False) as client:
        def post(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            response = client.post("/v1/chat", json=body, headers=DIAGNOSTICS)
            return response.status_code, response.json()

        rows = run(messages, post)

    print("     http +earlier route             ended          under 90%")
    for number, row in enumerate(rows, 1):
        print(show(number, row))
    cost = sum(row["costNusd"] for row in rows) / 1e9
    print(f"\n{len(rows)} turns, about ${cost:.5f} in Jev calls.")
    if not all(row["hasMetrics"] for row in rows):
        print("Some turns came back without metrics: is the Brain running with "
              "BRAIN_ENVIRONMENT=development?", file=sys.stderr)
    if args.out:
        args.out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
