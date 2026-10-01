"""Replay synthetic conversations through /v1/chat with no network/model calls.

This is a repeatable regression check of orchestration, not a blind evaluation
of language understanding. Each follow-up includes the actual preceding answer.
Source membership and expected text are checked separately from completion,
refusal, and error handling; no semantic-entailment score is claimed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "chat" / "regression.json"
SCORER_VERSION = "chat-regression-v1"
DIMENSIONS = ("contract", "support", "completion", "refusal", "error")
ClientFactory = Callable[[dict[str, Any]], TestClient]


def digest(value: object) -> str:
    """Canonical JSON digest; callers pass only public configuration, never keys."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def files_digest(paths: list[Path]) -> str:
    return digest({str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted(paths)})


def revision() -> dict[str, object]:
    """Git identity is supplemental; source-content digests include uncommitted edits."""
    try:
        commit = subprocess.run(  # noqa: S603 - fixed local command, no shell or input
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, check=True,
        ).stdout.strip()
        status = subprocess.run(  # noqa: S603 - fixed local command, no shell or input
            ["/usr/bin/git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
            text=True, check=True,
        ).stdout
        return {"commit": commit, "dirty": bool(status)}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def fingerprints(cases: dict[str, Any], *, config: Mapping[str, object],
                 prompt: str, data: object) -> dict[str, object]:
    return {
        "git": revision(),
        "source_sha256": files_digest([p for p in (ROOT / "src").rglob("*")
                                        if p.suffix in {".py", ".json"}]),
        "config_sha256": digest(config),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "scorer_sha256": files_digest([Path(__file__), ROOT / "scripts" / "chat_fixtures.py"]),
        "cases_sha256": digest(cases),
        "data_sha256": digest(data),
    }


def score(status: int, body: dict[str, Any], expected: dict[str, Any],
          request_header: str | None) -> dict[str, list[str]]:
    """List violations per dimension. Empty lists mean those assertions passed.

    A missing assertion is not evidence of quality: reports also carry the entire
    expected object, and the suite metadata states the limits of the scorer.
    """
    failures: dict[str, list[str]] = {name: [] for name in DIMENSIONS}
    if status != expected["http_status"]:
        failures["contract"].append(f"HTTP {status}, expected {expected['http_status']}")
    request_id = body.get("requestId")
    if not isinstance(request_id, str) or not request_id or request_header != request_id:
        failures["contract"].append("missing or mismatched request ID")
    answer = body.get("answer", "")
    if not isinstance(answer, str):
        failures["contract"].append("answer must be text")
        answer = ""
    if status == 200:
        if not answer.strip():
            failures["contract"].append("successful response has no answer")
        if body.get("status") not in {"answered", "partial", "clarification", "unavailable"}:
            failures["contract"].append("successful response has an invalid answer status")
        if "citations" not in body:
            failures["contract"].append("successful response has no citations field")
    if "status" in expected and body.get("status") != expected["status"]:
        failures["completion"].append(f"answer status is {body.get('status')!r}")
    for text in expected.get("contains", []):
        if text.casefold() not in answer.casefold():
            failures["completion"].append(f"missing requested content: {text}")
    for text in expected.get("excludes", []):
        if text.casefold() in answer.casefold():
            failures["support"].append(f"forbidden claim: {text}")
    for text in expected.get("refusal_contains", []):
        if text.casefold() not in answer.casefold():
            failures["refusal"].append(f"missing account limitation: {text}")
    for text in expected.get("refusal_excludes", []):
        if text.casefold() in answer.casefold():
            failures["refusal"].append(f"unnecessary refusal: {text}")
    citations = body.get("citations", [])
    if not isinstance(citations, list) or any(not isinstance(c, dict) for c in citations):
        failures["contract"].append("citations must be objects in a list")
        citations = []
    got_ids = {c.get("id") for c in citations if isinstance(c.get("id"), str)}
    if any(not all(isinstance(c.get(key), str) and c[key] for key in ("id", "title", "url"))
           for c in citations):
        failures["contract"].append("citation is missing required text fields")
    if got_ids and not isinstance(body.get("datasetVersion"), str):
        failures["contract"].append("factual response has no dataset version")
    wanted = set(expected.get("citation_ids", []))
    if not wanted.issubset(got_ids):
        failures["support"].append(f"missing citations: {sorted(wanted - got_ids)}")
    if "allowed_citation_ids" in expected:
        extra = got_ids - set(expected["allowed_citation_ids"])
        if extra:
            failures["support"].append(f"unrecognized citations: {sorted(extra)}")
    citation_urls = {c.get("url") for c in citations if isinstance(c.get("url"), str)}
    for url in re.findall(r"\]\((https?://[^\s)]+)\)", answer):
        if url not in citation_urls:
            failures["support"].append(f"uncited answer link: {url}")
    error = body.get("error")
    if status >= 400 and (not isinstance(error, dict)
                          or not isinstance(error.get("code"), str)
                          or not isinstance(error.get("message"), str)
                          or not isinstance(error.get("retryable"), bool)):
        failures["contract"].append("failure has an invalid error envelope")
    if "error_code" in expected:
        if not isinstance(error, dict) or error.get("code") != expected["error_code"]:
            failures["error"].append("wrong or missing error code")
        elif error.get("retryable") is not expected.get("retryable", False):
            failures["error"].append("wrong retry behavior")
        if "next_allowance_at" in expected and (
            not isinstance(error, dict)
            or error.get("nextAllowanceAt") != expected["next_allowance_at"]
        ):
            failures["error"].append("wrong or missing next allowance date")
    elif error is not None:
        failures["error"].append("unexpected error")
    if "limitation_code" in expected:
        limitation = body.get("limitation")
        if (not isinstance(limitation, dict)
                or limitation.get("code") != expected["limitation_code"]):
            failures["error"].append("wrong or missing partial-answer limitation")
    return failures


def run(suite: dict[str, Any], factory: ClientFactory) -> list[dict[str, Any]]:
    results = []
    for conversation in suite["conversations"]:
        history: list[dict[str, str]] = []
        with factory(conversation) as client:
            for index, turn in enumerate(conversation["turns"], 1):
                history.append({"role": "user", "content": turn["user"]})
                request = {"messages": list(history),
                           "omittedMessages": turn.get("omitted_messages", 0)}
                started = perf_counter()
                response = client.post("/v1/chat", json=request)
                elapsed_ms = round((perf_counter() - started) * 1000, 2)
                body = response.json()
                failures = score(response.status_code, body, turn["expected"],
                                 response.headers.get("x-request-id"))
                results.append({"conversation": conversation["id"], "turn": index,
                                "request": request, "http_status": response.status_code,
                                "body": body, "expected": turn["expected"],
                                "failures": failures, "elapsed_ms": elapsed_ms})
                if isinstance(body.get("answer"), str) and body["answer"]:
                    history.append({"role": "assistant", "content": body["answer"]})
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, object]:
    assertion_keys = {
        "contract": {"http_status"},
        "support": {"citation_ids", "allowed_citation_ids", "excludes"},
        "completion": {"status", "contains"}, "refusal": {"refusal_contains", "refusal_excludes"},
        "error": {"http_status"},
    }
    return {
        "turns": len(results),
        "passed": sum(not any(r["failures"].values()) for r in results),
        "turns_with_assertions": {name: sum(bool(keys & r["expected"].keys()) for r in results)
                                  for name, keys in assertion_keys.items()},
        "failures_by_dimension": {
            name: [{"conversation": r["conversation"], "turn": r["turn"]}
                   for r in results if r["failures"][name]] for name in DIMENSIONS
        },
        "all_regressions_pass": bool(results) and all(not any(r["failures"].values())
                                                     for r in results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error("output already exists; choose a new report path to preserve evidence")
    # Imports are intentionally local: no provider credentials or environment files
    # are loaded, and the adapter contains no HTTP transport.
    from chat_fixtures import PUBLIC_CONFIG, make_client, prompt_text

    suite = json.loads(args.cases.read_text())
    results = run(suite, lambda conversation: make_client(conversation, suite["fixtures"]))
    report = {
        "kind": "offline_http_regression", "blind": False, "paid_calls": 0,
        "support_method": "expected text and citation membership; no semantic entailment score",
        "created_at": datetime.now(UTC).isoformat(), "scorer_version": SCORER_VERSION,
        "limitations": ["scripted model responses do not test interpretation",
                        "synthetic facts do not test published campus data",
                        "expected-text and citation checks do not prove semantic entailment",
                        "offline timings do not measure provider or production latency"],
        "fingerprints": fingerprints(suite, config=PUBLIC_CONFIG, prompt=prompt_text(),
                                     data=suite.get("fixtures", {})),
        "summary": summarize(results), "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report["summary"], indent=2))
    return 0 if report["summary"]["all_regressions_pass"] else 1  # type: ignore[index]


if __name__ == "__main__":
    raise SystemExit(main())
