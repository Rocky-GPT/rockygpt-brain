"""Fresh bounded development runs through the real paid gateway. Never retries.

Use synthetic cases only. This records raw evidence for independent semantic
review; a completed HTTP/model contract is not automatically a semantic pass.
"""

import argparse
import hashlib
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dotenv import load_dotenv

from rockygpt_brain.config import RELEASE, configuration_hash, load_deployment
from rockygpt_brain.contracts import ChatMessage, ChatRequest
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.provider import ModelClient, ModelResponse, open_gateway
from rockygpt_brain.core.render import InvalidAnswer
from rockygpt_brain.governance.accounting import (
    CAMPUS_ZONE,
    Category,
    PaidCallError,
    PostgresLedger,
    month_at,
)
from rockygpt_brain.retrieval.data import CampusData, ReadQuery, SearchQuery
from rockygpt_brain.retrieval.exact import ContactQuery


class CapturedModel:
    """Keep structured drafts/verdicts for synthetic evaluation, never model reasoning."""

    def __init__(self, client: ModelClient) -> None:
        self.client = client
        self.outputs: list[dict[str, Any]] = []

    def create(self, *, category: Category, **kwargs: Any) -> ModelResponse:
        response = self.client.create(category=category, **kwargs)
        self.outputs.append(
            {
                "category": category,
                "status": response.status,
                "outputText": response.output_text,
                "answerMessages": [
                    item.model_dump() for item in response.output if item.type == "message"
                ],
                "toolCalls": [
                    item.model_dump() for item in response.output if item.type == "function_call"
                ],
            }
        )
        return response


class CapturedData(CampusData):
    def __init__(self, url: str, now: datetime) -> None:
        super().__init__(url, now)
        self.outputs: list[dict[str, Any]] = []

    def search(self, query: SearchQuery) -> dict[str, Any]:
        result = super().search(query)
        self.outputs.append(
            {"tool": "search_campus", "arguments": query.model_dump(), "output": result}
        )
        return result

    def read(self, query: ReadQuery) -> dict[str, Any]:
        result = super().read(query)
        self.outputs.append(
            {"tool": "read_campus", "arguments": query.model_dump(), "output": result}
        )
        return result

    def lookup_contact(self, query: ContactQuery) -> dict[str, Any]:
        result = super().lookup_contact(query)
        self.outputs.append(
            {"tool": "lookup_contact", "arguments": query.model_dump(), "output": result}
        )
        return result


def prepare_requests(
    cases: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], int, dict[str, Any] | None]]:
    """Validate all static user turns before paid work; follow-ups use real generated history."""
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("A nonempty corpus with unique case IDs is required")
    requests: list[tuple[dict[str, Any], int, dict[str, Any] | None]] = []
    for case in cases:
        turns = case.get("turns")
        if turns is None:
            ChatRequest.model_validate({"messages": case["messages"]})
            requests.append((case, 1, None))
        else:
            if not turns or (case["messages"] and case["messages"][-1]["role"] != "assistant"):
                raise ValueError(
                    "Live conversations require turns and an empty or assistant-ending seed"
                )
            for index, turn in enumerate(turns, 1):
                ChatRequest.model_validate(
                    {"messages": [*case["messages"], {"role": "user", "content": turn["user"]}]}
                )
                requests.append((case, index, turn))
    return requests


def execute_http(
    base_url: str,
    messages: list[ChatMessage],
    data: CapturedData,
    ledger: PostgresLedger,
    source_hash: str,
    *,
    stream: bool = False,
) -> dict[str, Any]:
    """Actual local HTTP path; reconstruct frozen evidence and verify its IDs.

    Replayed database results are explicitly labeled, not represented as captured
    model inputs. No model call is made by this evaluation process in HTTP mode.
    """
    headers = {}
    token = os.environ.get("STAGING_SERVICE_TOKEN", "").strip()
    if token:
        headers["X-RockyGPT-Environment-Token"] = token
    if stream:
        headers["Accept"] = "text/event-stream"
    progress: list[dict[str, Any]] = []
    started = monotonic()
    with httpx.Client(timeout=60, trust_env=False) as client:
        request = {"messages": [message.model_dump() for message in messages]}
        url = base_url.rstrip("/") + "/v1/chat"
        if stream:
            with client.stream("POST", url, headers=headers, json=request) as response:
                if "text/event-stream" in response.headers.get("content-type", ""):
                    terminal = None
                    event_name = ""
                    event_lines: list[str] = []
                    for line in response.iter_lines():
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("data:"):
                            event_lines.append(line[5:].lstrip())
                        elif not line and event_lines:
                            value = json.loads("\n".join(event_lines))
                            if event_name == "progress":
                                progress.append(
                                    {"elapsedMs": round((monotonic() - started) * 1000), **value}
                                )
                            elif event_name == "result":
                                terminal = value
                                break
                            event_name, event_lines = "", []
                    if terminal is None:
                        raise ValueError(
                            "Stream ended without a final result; previews are not answers"
                        )
                    status, payload = terminal["status"], terminal["body"]
                else:
                    response.read()
                    status, payload = response.status_code, response.json()
        else:
            response = client.post(url, headers=headers, json=request)
            status, payload = response.status_code, response.json()
    elapsed = round((monotonic() - started) * 1000)
    request_id = payload["requestId"]
    operations = ledger.operations(request_id)
    settled = [op for op in operations if op["state"] == "settled"]
    row: dict[str, Any] = {
        "requestId": request_id,
        "httpStatus": status,
        "httpElapsedMs": elapsed,
        **({"progress": progress} if stream else {}),
        "evidenceOrigin": "replayed_frozen_trace_with_verified_ids",
        "usage": {
            "modelCalls": len(operations),
            "costNusd": sum(op["cost_nusd"] for op in settled),
            "unsettledNusd": sum(
                op["reserved_nusd"] for op in operations if op["state"] != "settled"
            ),
            "usageComplete": len(settled) == len(operations),
        },
        "operations": [
            {
                key: op[key]
                for key in (
                    "operation_id",
                    "category",
                    "state",
                    "reserved_nusd",
                    "cost_nusd",
                    "usage",
                    "elapsed_ms",
                    "returned_model",
                    "metadata",
                )
            }
            for op in operations
        ],
        "evidence": data.outputs,
    }
    if any(op["metadata"]["configuration_hash"] != source_hash for op in operations):
        row["error"] = "server_configuration_mismatch"
        row["response"] = payload
        return row
    if status != 200:
        row["error"] = payload.get("reason", payload.get("error", {}).get("code", "http_error"))
        row["response"] = payload
        return row
    for trace in payload.get("trace", []):
        if trace["status"] not in {"ok", "no_match"}:
            continue
        query = trace["arguments"]
        if trace["tool"] == "search_campus":
            output = data.search(SearchQuery.model_validate(query))
        elif trace["tool"] == "read_campus":
            output = data.read(ReadQuery.model_validate(query))
        elif trace["tool"] == "lookup_contact":
            output = data.lookup_contact(ContactQuery.model_validate(query))
        else:
            continue
        if trace.get("reason") == "retrieval_delivery_limit":
            # The API reports the delivered prefix, not every record the query
            # could return. Reproduce that subset without crediting omitted
            # records as evidence available to the writer or checker.
            delivered = output["records"][: trace["result_count"]]
            output.update(
                records=delivered,
                truncated=True,
                reason="retrieval_delivery_limit",
                status=trace["status"],
            )
        if payload.get("datasetVersion") != output.get("dataset_version") or trace[
            "evidence_ids"
        ] != [record["id"] for record in output["records"]]:
            row["error"] = "evidence_replay_mismatch"
            row["response"] = payload
            return row
    row["result"] = payload
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-run-cost-usd",
        type=Decimal,
        help="Stop before the next turn's worst-case reservation exceeds this run allowance.",
    )
    parser.add_argument(
        "--base-url", help="Use the actual local HTTP API instead of direct engine execution."
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Capture public progress and unverified previews using the real SSE transport.",
    )
    args = parser.parse_args()
    if args.stream and not args.base_url:
        parser.error("--stream requires --base-url")
    if args.base_url:
        target = urlsplit(args.base_url)
        if (
            target.scheme != "http"
            or target.hostname not in {"127.0.0.1", "localhost"}
            or target.username
            or target.password
            or target.query
            or target.fragment
        ):
            raise SystemExit("HTTP evaluation is restricted to a local development API.")
    if args.max_run_cost_usd is not None and (
        not args.max_run_cost_usd.is_finite() or args.max_run_cost_usd <= 0
    ):
        raise SystemExit("Run allowance must be a finite positive dollar amount.")
    if args.output.exists():
        raise SystemExit("Refusing to replace an existing report; retain failed runs.")
    load_dotenv(".env")
    deployment = load_deployment()
    if deployment.environment != "development":
        raise SystemExit("Development environment required.")
    corpus = json.loads(args.cases.read_text())
    cases = corpus["cases"] if isinstance(corpus, dict) else corpus
    requests = prepare_requests(cases)
    histories = {case["id"]: list(case["messages"]) for case in cases}
    failed_conversations: set[str] = set()
    now = datetime.now(CAMPUS_ZONE)
    ledger = PostgresLedger(deployment.ledger_url, deployment.environment)
    with ledger.transaction() as conn:
        account = ledger.account(conn)
        totals = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN state <> 'settled' THEN reserved_nusd "
            "WHEN charged_month >= %s THEN cost_nusd ELSE 0 END),0) AS committed "
            "FROM brain_ops.operations WHERE environment=%s",
            (month_at(now), deployment.environment),
        ).fetchone()
        assert totals is not None
        remaining = ledger.monthly_cap(conn, account, now) - int(totals["committed"])
        ceiling = len(requests) * RELEASE.max_turn_cost_nusd
        run_allowance = (
            int(args.max_run_cost_usd * 1_000_000_000)
            if args.max_run_cost_usd is not None
            else ceiling
        )
        if (
            account["paused"]
            or run_allowance > remaining
            or run_allowance < RELEASE.max_turn_cost_nusd
        ):
            raise SystemExit("Run cannot fit the remaining allowance at its conservative ceiling.")
    source_hash = configuration_hash()
    report: dict[str, Any] = {
        "configurationHash": source_hash,
        "release": RELEASE.model_dump(mode="json"),
        "startedAt": now.isoformat(),
        "requested": len(requests),
        "requestedConversations": len(cases),
        "notRun": [],
        "corpusHash": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "preflight": {
            "remainingNusd": remaining,
            "requestedWorstCaseNusd": ceiling,
            "runCeilingNusd": run_allowance,
            "admission": "per-turn maximum plus settled and uncertain previous calls",
        },
        "semanticReview": "pending",
        "transport": "http-sse" if args.stream else "http" if args.base_url else "engine",
        "runs": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint() -> None:
        report["completed"] = sum("result" in row for row in report["runs"])
        report["admitted"] = sum(row["usage"]["modelCalls"] > 0 for row in report["runs"])
        report["costNusd"] = sum(row["usage"]["costNusd"] for row in report["runs"])
        report["unsettledNusd"] = sum(row["usage"]["unsettledNusd"] for row in report["runs"])
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")

    checkpoint()
    for request_index, (case, turn_index, turn) in enumerate(requests):
        committed = report["costNusd"] + report["unsettledNusd"]
        if committed + RELEASE.max_turn_cost_nusd > run_allowance:
            report["stoppedReason"] = "run_allowance_exhausted"
            report["notRun"].extend(
                {"caseId": pending["id"], "turn": index, "reason": "run_allowance_exhausted"}
                for pending, index, _ in requests[request_index:]
            )
            break
        if case["id"] in failed_conversations:
            report["notRun"].append(
                {"caseId": case["id"], "turn": turn_index, "reason": "previous_turn_failed"}
            )
            checkpoint()
            continue
        history = histories[case["id"]]
        if turn is not None:
            history.append({"role": "user", "content": turn["user"]})
        messages = ChatRequest.model_validate({"messages": history}).messages
        if configuration_hash() != source_hash:
            report["stoppedReason"] = "configuration_changed"
            report["notRun"].extend(
                {"caseId": pending["id"], "turn": index, "reason": "configuration_changed"}
                for pending, index, _ in requests[request_index:]
            )
            break
        request_id = str(uuid4())
        now = datetime.now(CAMPUS_ZONE)
        data = CapturedData(os.environ["DATABASE_URL"], now)
        metrics: dict[str, Any] = {}
        row = {
            **case,
            "id": case["id"] if turn is None else f"{case['id']}:turn-{turn_index}",
            "caseId": case["id"],
            "turn": turn_index,
            "messages": [message.model_dump() for message in messages],
            "expected_behaviors": (turn or {}).get(
                "expected_behaviors", case.get("expected_behaviors")
            ),
            "assertions": {**case.get("assertions", {}), **(turn or {}).get("assertions", {})},
            "requestId": request_id,
            "campusTime": now.isoformat(),
        }
        try:
            if args.base_url:
                row.update(
                    execute_http(
                        args.base_url, messages, data, ledger, source_hash, stream=args.stream
                    )
                )
                report["runs"].append(row)
                checkpoint()
            else:
                with open_gateway(deployment, request_id) as gateway:
                    captured_model = CapturedModel(gateway)
                    try:
                        row["result"] = run_turn(
                            messages,
                            client=captured_model,
                            data=data,
                            model=RELEASE.model,
                            now=now,
                            metrics=metrics,
                        )
                    except (PaidCallError, InvalidAnswer, TimeoutError) as error:
                        row["error"] = getattr(error, "code", "model_timeout")
                    except BaseException:
                        row["error"] = "evaluation_interrupted"
                        report["stoppedReason"] = "evaluation_interrupted"
                        raise
                    finally:
                        row["usage"] = gateway.usage.report()
                        row["evidence"] = data.outputs
                        row["modelOutputs"] = captured_model.outputs
                        report["runs"].append(row)
                        checkpoint()
                        gateway.finish(
                            {
                                "status": row.get("error") or row.get("result", {}).get("status"),
                                "evaluation": case["id"],
                                "configurationHash": source_hash,
                                "metrics": row.get("result", {}).get("metrics", metrics),
                            }
                        )
        finally:
            data.close()
        print(
            json.dumps(
                {
                    "id": case["id"],
                    "error": row.get("error"),
                    "status": row.get("result", {}).get("status"),
                    "usage": row["usage"],
                }
            ),
            flush=True,
        )
        if "result" in row:
            history.append({"role": "assistant", "content": row["result"]["answer"]})
        else:
            failed_conversations.add(case["id"])
        if row.get("error") in {
            "model_quota_exhausted",
            "rate_limited",
            "budget_exhausted",
            "accounting_paused",
            "accounting_unavailable",
            "server_configuration_mismatch",
            "evidence_replay_mismatch",
        }:
            report["stoppedReason"] = row["error"]
            report["notRun"].extend(
                {"caseId": pending["id"], "turn": index, "reason": row["error"]}
                for pending, index, _ in requests[request_index + 1 :]
            )
            break
    report["endingConfigurationHash"] = configuration_hash()
    checkpoint()


if __name__ == "__main__":
    main()
