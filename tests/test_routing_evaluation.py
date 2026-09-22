"""Promotion requires measured gains and a review bound to the actual outputs."""

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest
from evaluate_routing import CASES, promotion

from rockygpt_brain.config import configuration_hash


def report_and_quality() -> tuple[dict[str, Any], dict[str, Any]]:
    pairs = []
    cases = json.loads(CASES.read_text())["cases"]
    for case in cases:
        pairs.append(
            {
                "id": case["id"] + ":0",
                "routes": case["routes"],
                "eligible_direct": case["eligible_direct"],
                "off": {
                    "usageComplete": True,
                    "datasetVersion": "one",
                    "costNusd": 1000,
                    "elapsedMs": 1000,
                },
                "active": {
                    "usageComplete": True,
                    "datasetVersion": "one",
                    "costNusd": 100,
                    "elapsedMs": 200,
                    "routing": {"route": case["routes"][0], "directRetrieval": True},
                },
            }
        )
    digest = hashlib.sha256(json.dumps(pairs, sort_keys=True).encode()).hexdigest()
    quality = {
        "reportDigest": digest,
        "pairs": {pair["id"]: {"baselinePass": True, "activePass": True} for pair in pairs},
    }
    return {
        "pairs": pairs,
        "status": "evaluated",
        "repeats": 1,
        "configurationHash": configuration_hash(),
        "caseSetHash": hashlib.sha256(CASES.read_bytes()).hexdigest(),
    }, quality


def test_promotion_requires_quality_review_even_with_measured_savings() -> None:
    report, quality = report_and_quality()
    assert promotion(report, {})["developmentEligible"] is False
    assert promotion(report, quality)["productionEligible"] is True
    quality["pairs"][report["pairs"][0]["id"]]["activePass"] = False
    assert promotion(report, quality)["developmentEligible"] is False


def test_review_cannot_be_reused_for_different_results() -> None:
    report, quality = report_and_quality()
    report["pairs"][0]["active"]["answer"] = "Changed output"
    assert promotion(report, quality)["qualityReviewComplete"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"usageComplete": False},
        {"error": "budget_exhausted"},
        {"datasetVersion": "new-release"},
        {"costNusd": 1001},
        {"elapsedMs": 1001},
    ],
)
def test_failed_or_regressed_runs_cannot_be_promoted(change: dict[str, Any]) -> None:
    report, quality = report_and_quality()
    for pair in report["pairs"]:
        pair["active"].update(deepcopy(change))
    quality["reportDigest"] = hashlib.sha256(
        json.dumps(report["pairs"], sort_keys=True).encode()
    ).hexdigest()
    assert promotion(report, quality)["developmentEligible"] is False


def test_precision_below_95_percent_blocks_promotion() -> None:
    report, quality = report_and_quality()
    for pair in report["pairs"][:2]:
        pair["active"]["routing"]["route"] = "search"
    quality["reportDigest"] = hashlib.sha256(
        json.dumps(report["pairs"], sort_keys=True).encode()
    ).hexdigest()
    result = promotion(report, quality)
    assert result["supportedRoutePrecision"] < 0.95
    assert not result["developmentEligible"]


def test_no_direct_samples_or_no_classifications_cannot_pass() -> None:
    report, quality = report_and_quality()
    for pair in report["pairs"]:
        pair["active"]["routing"] = {"route": "unresolved", "directRetrieval": False}
    result = promotion(report, quality)
    assert result["supportedRoutePrecision"] is None
    assert not result["developmentEligible"]


@pytest.mark.parametrize(
    "change",
    [
        {"status": "incomplete"},
        {"repeats": 0},
        {"configurationHash": "old"},
        {"caseSetHash": "old"},
    ],
)
def test_incomplete_or_stale_evaluations_do_not_promote(change: dict[str, Any]) -> None:
    report, quality = report_and_quality()
    report.update(change)
    assert not promotion(report, quality)["developmentEligible"]


def test_partial_corpus_cannot_promote() -> None:
    report, quality = report_and_quality()
    report["pairs"].pop()
    quality["reportDigest"] = hashlib.sha256(
        json.dumps(report["pairs"], sort_keys=True).encode()
    ).hexdigest()
    assert not promotion(report, quality)["completeCorpus"]
    assert not promotion(report, quality)["developmentEligible"]
