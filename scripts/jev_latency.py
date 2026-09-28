"""Time Jev routing calls past the turn's 2 s window, to find where the slow ones go.

On 2026-09-28 about 1 in 5 routing calls on the dev Brain ran out the 2 s window and
the turn fell back to GPT. Successful calls took about 0.3 s. This sends the payload the
Brain builds for each routing case (same shortlist, same questions) several times with
a long timeout. It records how long each HTTP phase took: connect, TLS, sending, waiting
for the reply, and reading it. A one-question call runs after every case call as a
control. If the control is slow at the same moments, the service is slow, not our
questions. If one case is slow every time, its payload is the cause.

With `--hedge SECONDS`, each case call gets a copy sent SECONDS later if it hasn't
answered by then, and both run to the end. The report says how often the copy answered
first and how many calls either copy answered within the Brain's 2 s, against the first
alone. A copy that is fast while the first is stuck means slowness hits single
requests, so sending a copy saves time. Both slow means the whole service is slow.

Every call goes through the dev ledger like any routing call. Jev bills input tokens only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from statistics import median
from time import monotonic
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from rockygpt_brain.config import RELEASE, load_deployment
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.provider import (
    JEV_URLS,
    OPENROUTER_MODELS,
    JevProvider,
    ModelResponse,
    PaidGateway,
    Usage,
    input_bound,
    jev_tls,
)
from rockygpt_brain.core.routing import routing_payload, shortlist
from rockygpt_brain.governance.accounting import PaidCallError, PostgresLedger
from rockygpt_brain.retrieval.data import CampusData

CASES = Path(__file__).parents[1] / "docs/routing/cases.json"
CONTROL = {
    "latest_request": "hello",
}
CONTROL_QUESTIONS = {
    "greets": {
        "type": "noul",
        "instructions": "Does `latest_request` greet someone?",
        "criteria": {"true": "It says hello or greets someone", "false": "It does not"},
    },
}
# httpx trace events that open and close each phase, as (phase, started, completed).
# `connect` includes the DNS lookup.
PHASES = (
    ("setup", "start", "connection.connect_tcp.started"),
    ("connect", "connection.connect_tcp.started", "connection.connect_tcp.complete"),
    ("tls", "connection.start_tls.started", "connection.start_tls.complete"),
    ("send", "http11.send_request_headers.started", "http11.send_request_body.complete"),
    ("wait", "http11.send_request_body.complete", "http11.receive_response_headers.complete"),
    ("read", "http11.receive_response_body.started", "http11.receive_response_body.complete"),
)


class TimedJev(JevProvider):
    """JevProvider's one request, with the time each HTTP phase took kept in `last`."""

    last: dict[str, Any]

    def create(self, *, timeout: float, **payload: Any) -> ModelResponse:
        pinned: str = payload["model"]
        requested, reported = (
            OPENROUTER_MODELS[pinned] if self.name == "openrouter" else (pinned, pinned)
        )
        marks: dict[str, float] = {"start": 0.0}
        self.last = {"marks": marks}
        started = monotonic()

        async def trace(event: str, _info: dict[str, Any]) -> None:
            marks[event] = monotonic() - started

        async def request() -> ModelResponse:
            async with httpx.AsyncClient(
                trust_env=False, timeout=timeout, verify=jev_tls()
            ) as client:
                async with client.stream(
                    "POST", JEV_URLS[self.name], json={**payload, "model": requested},
                    headers={"Authorization": "Bearer " + self._api_key},
                    extensions={"trace": trace},
                ) as response:
                    self.last["status"] = response.status_code
                    self.last["headers"] = {
                        key: value for key, value in response.headers.items()
                        if key.startswith(("x-", "server", "cf-", "via", "age"))
                    }
                    response.raise_for_status()
                    raw = json.loads(await response.aread())
                    usage = raw.get("usage", {})
                    model = str(raw.get("model", ""))
                    return ModelResponse(
                        str(raw.get("id") or response.headers.get("x-request-id", "")),
                        pinned if model == reported else model, "completed",
                        json.dumps(raw.get("answers")),
                        [], Usage(usage["input_tokens"], 0, usage["output_tokens"], 0),
                    )

        try:
            return asyncio.run(asyncio.wait_for(request(), timeout=timeout))
        finally:
            self.last["totalMs"] = round((monotonic() - started) * 1000)
            self.last["phasesMs"] = {
                phase: round((marks[end] - marks[begin]) * 1000)
                for phase, begin, end in PHASES if begin in marks and end in marks
            }
            # The phase a call was stuck in: the last one that started without finishing.
            open_phases = [phase for phase, begin, end in PHASES
                           if begin in marks and end not in marks]
            self.last["stuckIn"] = open_phases[-1] if open_phases else None
            del self.last["marks"]


def payload_stats(payload: dict[str, Any], candidates: int) -> dict[str, Any]:
    questions = payload["questions"]
    return {
        "questions": len(questions),
        "choiceOptions": sum(len(question.get("criteria", {})) for question in questions.values()
                             if question["type"] == "choice"),
        "entityCandidates": candidates,
        "bytes": input_bound(payload) - 8192,
    }


def call(ledger: PostgresLedger, jev: TimedJev, payload: dict[str, Any],
         timeout: float) -> dict[str, Any]:
    release = RELEASE.model_copy(update={
        "routing": RELEASE.routing.model_copy(update={"timeout_seconds": timeout}),
    })
    result: dict[str, Any] = {}
    with ledger.session():
        gateway = PaidGateway(None, ledger, "jev-latency-" + str(uuid4()),  # type: ignore[arg-type]
                              release=release, routing_provider=jev)
        try:
            gateway.route(payload, timeout=timeout)
            result["ok"] = True
        except PaidCallError as error:
            result.update(ok=False, error=error.code, cause=type(error.__cause__).__name__)
        usage = gateway.usage.report()
        result.update(jev.last, inputTokens=usage["inputTokens"], costNusd=usage["costNusd"])
    return result


def hedged(ledgers: tuple[PostgresLedger, PostgresLedger], jevs: tuple[TimedJev, TimedJev],
           payload: dict[str, Any], timeout: float, delay: float) -> dict[str, Any]:
    """A call, and a copy sent `delay` seconds later if the first hasn't answered."""
    with ThreadPoolExecutor(2) as pool:
        started = monotonic()
        first = pool.submit(call, ledgers[0], jevs[0], payload, timeout)
        done, _ = wait([first], timeout=delay)
        second, offset = None, None
        if not done:
            offset = round((monotonic() - started) * 1000)
            second = pool.submit(call, ledgers[1], jevs[1], payload, timeout)
        row: dict[str, Any] = {"first": first.result(), "copyAtMs": offset,
                               "copy": second.result() if second else None}
    # When each copy's answer reached us, counted from the first's start.
    answered = [row["first"]["totalMs"]] if row["first"]["ok"] else []
    if row["copy"] and row["copy"]["ok"]:
        answered.append(offset + row["copy"]["totalMs"])
    row["ok"] = bool(answered)
    row["totalMs"] = min(answered) if answered else row["first"]["totalMs"]
    row["copyWon"] = bool(answered) and row["first"]["totalMs"] != row["totalMs"]
    return row


def hedge_summary(rows: list[dict[str, Any]], within: float) -> dict[str, Any]:
    items = [row for row in rows if row["kind"] == "hedge"]
    copied = [row for row in items if row["copy"]]
    limit = within * 1000

    def in_time(result: dict[str, Any]) -> bool:
        return bool(result["ok"]) and result["totalMs"] <= limit

    return {
        "calls": len(items),
        "copiesSent": len(copied),
        "copyWon": sum(row["copyWon"] for row in items),
        f"firstAloneWithin{within}s": sum(in_time(row["first"]) for row in items),
        f"eitherWithin{within}s": sum(in_time(row) for row in items),
        # Of the calls a copy was sent for: was the copy itself fast (independent
        # slowness) or slow too (the whole service)?
        f"copyWithin{within}sOfItsStart": sum(in_time(row["copy"]) for row in copied),
        "firstMs": sorted(row["first"]["totalMs"] for row in items),
        "eitherMs": sorted(row["totalMs"] for row in items),
        "costNusd": sum(row["first"].get("costNusd", 0)
                        + (row["copy"] or {}).get("costNusd", 0) for row in items),
    }


def summary(rows: list[dict[str, Any]], slow: float) -> dict[str, Any]:
    def spread(values: list[float]) -> dict[str, float] | None:
        return {"min": min(values), "median": median(values), "max": max(values)} \
            if values else None

    report: dict[str, Any] = {}
    for kind in ("case", "control"):
        items = [row for row in rows if row["kind"] == kind]
        slow_items = [row for row in items if row["totalMs"] >= slow * 1000]
        report[kind] = {
            "calls": len(items),
            "failed": sum(not row["ok"] for row in items),
            f"atLeast{slow}s": len(slow_items),
            "totalMs": spread([row["totalMs"] for row in items]),
            "slowStuckIn": {phase: sum(row.get("stuckIn") == phase for row in slow_items)
                            for phase in {row.get("stuckIn") for row in slow_items}},
            "fastPhasesMs": {phase: spread([row["phasesMs"][phase] for row in items
                                            if row["totalMs"] < slow * 1000
                                            and phase in row["phasesMs"]])
                             for phase, _, _ in PHASES},
            "slowPhasesMs": {phase: spread([row["phasesMs"][phase] for row in slow_items
                                            if phase in row["phasesMs"]])
                             for phase, _, _ in PHASES},
        }
    cases: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["kind"] == "case":
            cases.setdefault(row["case"], []).append(row)
    report["perCase"] = {
        case: {**items[0]["payload"], "totalMs": [row["totalMs"] for row in items],
               "slowCalls": sum(row["totalMs"] >= slow * 1000 for row in items)}
        for case, items in cases.items()
    }
    # A case slow on every call points at its payload; slow calls spread over every case,
    # with slow controls beside them, point at the service.
    report["casesSlowEveryTime"] = sorted(
        case for case, item in report["perCase"].items()
        if item["slowCalls"] == len(item["totalMs"]))
    report["casesSlowSometimes"] = sorted(
        case for case, item in report["perCase"].items()
        if 0 < item["slowCalls"] < len(item["totalMs"]))
    report["costNusd"] = sum(row.get("costNusd", 0) for row in rows)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="Seconds each call may take, up to 15 (the Brain allows 2)")
    parser.add_argument("--slow", type=float, default=1.5,
                        help="Seconds from which a call counts as slow")
    parser.add_argument("--only", nargs="*", help="Case IDs from docs/routing/cases.json")
    parser.add_argument("--hedge", type=float,
                        help="Send a copy of each case call this many seconds after it if it "
                        "hasn't answered, instead of the one-question control call")
    parser.add_argument("--env-file", type=Path,
                        help="Settings file to load (default: .env in the working directory)")
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv(args.env_file)
    if os.environ.get("BRAIN_ENVIRONMENT") != "development":
        parser.error("Runs only with BRAIN_ENVIRONMENT=development")
    if not 1 <= args.repeats <= 5 or not 0 < args.timeout <= 15:
        parser.error("--repeats must be 1-5 and --timeout 0-15 seconds")
    if args.hedge is not None and not 0 < args.hedge < args.timeout:
        parser.error("--hedge must be between 0 and --timeout seconds")
    if args.output.exists():
        parser.error("--output exists; reports are never overwritten")
    deployment = load_deployment().model_copy(update={"routing_mode": "active"})
    assert deployment.routing_api_key is not None, "No Jev credential"
    jev = TimedJev(deployment.routing_api_key, deployment.routing_provider)
    ledger = PostgresLedger(deployment.ledger_url, deployment.environment)
    # A copy runs beside its call, so it needs its own ledger connection and timings.
    jevs = (jev, TimedJev(deployment.routing_api_key, deployment.routing_provider))
    ledgers = (ledger, PostgresLedger(deployment.ledger_url, deployment.environment))
    cases = [case for case in json.loads(CASES.read_text())["cases"]
             if not args.only or case["id"] in args.only]
    now = datetime.now(ZoneInfo("America/New_York"))
    data = CampusData(os.environ["DATABASE_URL"], now)
    registry = data.identity_registry()
    entities = registry.entities if registry else []
    payloads = {}
    for case in cases:
        messages = [ChatMessage.model_validate(message) for message in case["messages"]]
        candidates = shortlist(entities, messages)
        payload, _ = routing_payload(messages, candidates, now)
        payloads[case["id"]] = (payload, payload_stats(payload, len(candidates)))
    data.close()
    control = {"model": RELEASE.routing.model, "state": CONTROL, "questions": CONTROL_QUESTIONS}
    rows: list[dict[str, Any]] = []
    order = [case["id"] for case in cases]
    for repeat in range(args.repeats):
        # A slow stretch of time hits different cases; not for security.
        random.Random(repeat).shuffle(order)  # noqa: S311
        for case_id in order:
            payload, stats = payloads[case_id]
            at = datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")
            if args.hedge is not None:
                row = {"kind": "hedge", "case": case_id, "repeat": repeat, "at": at,
                       "payload": stats,
                       **hedged(ledgers, jevs, payload, args.timeout, args.hedge)}
                rows.append(row)
                print(json.dumps({"case": case_id, "repeat": repeat, "ok": row["ok"],
                                  "firstMs": row["first"]["totalMs"],
                                  "copyMs": (row["copy"] or {}).get("totalMs"),
                                  "copyWon": row["copyWon"]}), flush=True)
            else:
                for kind, body in (("case", payload), ("control", control)):
                    row = {"kind": kind, "case": case_id, "repeat": repeat, "at": at,
                           **({"payload": stats} if kind == "case" else {}),
                           **call(ledger, jev, body, args.timeout)}
                    rows.append(row)
                    print(json.dumps({key: row[key] for key in
                                      ("kind", "case", "repeat", "ok", "totalMs", "stuckIn")}),
                          flush=True)
            args.output.write_text(json.dumps({"rows": rows}, indent=1) + "\n")
    report = {"rows": rows, "summary": hedge_summary(rows, RELEASE.routing.timeout_seconds)
              if args.hedge is not None else summary(rows, args.slow)}
    args.output.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
