"""Paired, budget-accounted Jev evaluation using fixed synthetic campus questions.

No mode is enabled by this script. Promotion additionally requires a completed
human quality review of every paired answer in the generated report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from time import monotonic
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from rockygpt_brain.config import RELEASE, RoutingMode, configuration_hash, load_deployment
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.provider import open_gateway
from rockygpt_brain.governance.accounting import PaidCallError
from rockygpt_brain.retrieval.data import CampusData

CASES = Path(__file__).parents[1] / "docs/routing/cases.json"


def percentile95(values: list[float]) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)]


def promotion(report: dict[str, Any], quality: dict[str, Any]) -> dict[str, Any]:
    pairs = report["pairs"]
    fixture = json.loads(CASES.read_text())["cases"]
    repeats = report.get("repeats", 0)
    expected_ids = (
        {f"{case['id']}:{repeat}" for case in fixture for repeat in range(repeats)}
        if type(repeats) is int and 1 <= repeats <= 5
        else set()
    )
    complete_corpus = bool(expected_ids) and (
        report.get("status") == "evaluated"
        and report.get("caseSetHash") == hashlib.sha256(CASES.read_bytes()).hexdigest()
        and len(pairs) == len(expected_ids)
        and {pair["id"] for pair in pairs} == expected_ids
    )
    same_config = report.get("configurationHash") == configuration_hash()
    successful = all("error" not in pair[mode] for pair in pairs for mode in ("off", "active"))
    complete_usage = all(
        pair[mode].get("usageComplete", False) for pair in pairs for mode in ("off", "active")
    )
    # The receipt hash prevents reusing a review of different model outputs.
    digest = hashlib.sha256(json.dumps(pairs, sort_keys=True).encode()).hexdigest()
    reviewed = quality.get("reportDigest") == digest and all(
        quality.get("pairs", {}).get(pair["id"], {}).get("baselinePass") is True
        and quality.get("pairs", {}).get(pair["id"], {}).get("activePass") is True
        for pair in pairs
    )
    same_release = all(
        pair["off"].get("datasetVersion") == pair["active"].get("datasetVersion") for pair in pairs
    )
    routed = [
        pair
        for pair in pairs
        if pair["active"].get("routing", {}).get("route") not in {None, "unresolved"}
    ]
    correct = sum(pair["active"]["routing"]["route"] in pair["routes"] for pair in routed)
    precision = correct / len(routed) if routed else None
    direct = [pair for pair in pairs if pair["eligible_direct"]]
    executed_direct = sum(
        bool(pair["active"].get("routing", {}).get("directRetrieval")) for pair in direct
    )

    def reduced(items: list[dict[str, Any]], key: str) -> bool:
        return bool(items) and mean(pair["active"][key] for pair in items) < mean(
            pair["off"][key] for pair in items
        )

    valid = (
        bool(pairs)
        and complete_corpus
        and same_config
        and successful
        and complete_usage
        and same_release
    )
    direct_gain = (
        valid
        and executed_direct > 0
        and reduced(direct, "costNusd")
        and reduced(direct, "elapsedMs")
    )
    development = valid and reviewed and precision is not None and precision >= 0.95 and direct_gain
    overall_gain = bool(
        valid
        and reduced(pairs, "costNusd")
        and median(pair["active"]["elapsedMs"] for pair in pairs)
        < median(pair["off"]["elapsedMs"] for pair in pairs)
    )
    p95_ok = bool(
        valid
        and percentile95([pair["active"]["elapsedMs"] for pair in pairs])
        <= 1.05 * percentile95([pair["off"]["elapsedMs"] for pair in pairs])
    )
    return {
        "reportDigest": digest,
        "qualityReviewComplete": reviewed,
        "completeCorpus": complete_corpus,
        "sameConfiguration": same_config,
        "completeUsage": complete_usage,
        "sameDataset": same_release,
        "supportedRoutePrecision": precision,
        "classifiedSamples": len(routed),
        "eligibleDirectSamples": len(direct),
        "directSamples": executed_direct,
        "directCostAndLatencyImproved": direct_gain,
        "overallCostAndMedianLatencyImproved": overall_gain,
        "p95WithinFivePercent": p95_ok,
        "developmentEligible": development,
        "productionEligible": development and overall_gain and p95_ok,
    }


def evaluate_case(case: dict[str, Any], mode: RoutingMode, now: datetime) -> dict[str, Any]:
    deployment = load_deployment().model_copy(update={"routing_mode": mode})
    data = CampusData(os.environ["DATABASE_URL"], now)
    request_id = "jev-eval-" + str(uuid4())
    started = monotonic()
    result: dict[str, Any] = {}
    try:
        with open_gateway(deployment, request_id) as gateway:
            try:
                response = run_turn(
                    [ChatMessage.model_validate(message) for message in case["messages"]],
                    client=gateway,
                    data=data,
                    model=RELEASE.model,
                    now=now,
                    routing_client=gateway if mode != "off" else None,
                    routing_mode=mode,
                )
                result = {
                    "status": response["status"],
                    "answer": response["answer"],
                    "citations": response["citations"],
                    "datasetVersion": response["datasetVersion"],
                    "routing": response["metrics"].get("routing", {}),
                    "validationFailures": response["metrics"].get("validationFailures", []),
                }
            except PaidCallError as error:
                result = {"error": error.code}
            except Exception:
                result = {"error": "evaluation_failed"}
            finally:
                result.update(
                    gateway.usage.report(), elapsedMs=round((monotonic() - started) * 1000)
                )
                # Persist text-free operational metrics, never the synthetic answer or input.
                gateway.finish(
                    {
                        "evaluation": "jev-routing",
                        "mode": mode,
                        "status": result.get("status", "unavailable"),
                        "routing": result.get("routing"),
                        "elapsedMs": result["elapsedMs"],
                    }
                )
    finally:
        data.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--quality-review", type=Path)
    parser.add_argument(
        "--report", type=Path, help="Recompute gates on a saved report; no paid calls"
    )
    args = parser.parse_args()
    quality = json.loads(args.quality_review.read_text()) if args.quality_review else {}
    if args.report:
        report = json.loads(args.report.read_text())
        report["gates"] = promotion(report, quality)
    else:
        load_dotenv()
        required = [
            "BRAIN_TYPESAFE_API_KEY",
            "DATABASE_URL",
            "BRAIN_ENVIRONMENT",
            "BRAIN_OPENAI_API_KEY",
            "BRAIN_OPENAI_PROJECT",
            "BRAIN_LEDGER_DATABASE_URL",
        ]
        missing = [key for key in required if not os.getenv(key)]
        if missing:
            report = {
                "status": "blocked",
                "missingEnvironmentVariables": missing,
                "routingEnabled": False,
                "paidCalls": 0,
            }
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report))
            return 2
        if os.environ["BRAIN_ENVIRONMENT"] != "development":
            parser.error("Live evaluation requires BRAIN_ENVIRONMENT=development")
        if not 1 <= args.repeats <= 5:
            parser.error("--repeats must be between 1 and 5")
        cases = json.loads(CASES.read_text())["cases"]
        pairs = []
        for repeat in range(args.repeats):
            for index, case in enumerate(cases):
                now = datetime.now(ZoneInfo("America/New_York"))
                pair: dict[str, Any] = {
                    "id": f"{case['id']}:{repeat}",
                    "routes": case["routes"],
                    "eligible_direct": case["eligible_direct"],
                }
                # Alternate order to reduce warming/order bias. Both use the same campus time.
                modes: tuple[RoutingMode, RoutingMode] = (
                    ("off", "active") if (index + repeat) % 2 == 0 else ("active", "off")
                )
                for mode in modes:
                    pair[mode] = evaluate_case(case, mode, now)
                pairs.append(pair)
                report = {
                    "status": "incomplete",
                    "repeats": args.repeats,
                    "caseSetHash": hashlib.sha256(CASES.read_bytes()).hexdigest(),
                    "configurationHash": configuration_hash(),
                    "routingVersion": RELEASE.routing.version,
                    "pairs": pairs,
                }
                args.output.write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps({"case": pair["id"], "completed": len(pairs)}), flush=True)
                if any("error" in pair[mode] for mode in modes):
                    report["status"] = "incomplete"
                    args.output.write_text(json.dumps(report, indent=2) + "\n")
                    return 1  # Stop on budget, accounting or upstream failure; no retry loop.
        report["status"] = "evaluated"
        report["gates"] = promotion(report, quality)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["gates"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
