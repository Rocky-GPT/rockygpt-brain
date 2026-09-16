"""Independent keyword/filter evaluation. No provider or model client is imported."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from phase2_snapshot import ROOT, local_database, snapshot

from rockygpt_brain.data import CampusData, ReadQuery, SearchQuery

frozen = snapshot()
cases = json.loads((ROOT / "docs/phase2/retrieval-cases.json").read_text())
data = CampusData(local_database(), datetime.fromisoformat(frozen["captured_at"]))
results = []
try:
    for case in cases:
        output = data.search(SearchQuery.model_validate(case["query"]))
        records = output["records"]
        ids = {r["id"] for r in records}
        details = (
            data.read(ReadQuery(ids=sorted(ids)))["records"]
            if ids and case.get("required_sources")
            else []
        )
        source_checks = []
        for requirement in case.get("required_sources", []):
            source_records = [r for r in records + details if r["url"] == requirement["url"]]
            text = " ".join(r.get("content", "") for r in source_records).casefold()
            missing = [
                phrase for phrase in requirement["contains"] if phrase.casefold() not in text
            ]
            source_checks.append(
                {
                    "url": requirement["url"],
                    "missing_qualifiers": missing,
                    "found": bool(source_records),
                    "fresh": all(r["freshness"] in {"fresh", "static"} for r in source_records),
                }
            )
        passed = bool(records)
        if "expected_entity" in case:
            passed = any(r["entity_id"] == case["expected_entity"] for r in records)
        if "expected_ids" in case:
            passed = set(case["expected_ids"]) <= ids
        if case.get("expect_empty"):
            passed = not records
        for key, value in case.get("every_field", {}).items():
            passed = passed and all(r["fields"].get(key) == value for r in records)
        if case.get("every_date"):
            passed = passed and all(r["valid_from"] == case["every_date"] for r in records)
        if source_checks:
            passed = passed and all(
                c["found"] and c["fresh"] and not c["missing_qualifiers"] for c in source_checks
            )
        results.append(
            {
                "id": case["id"],
                "evidence_retrieved": passed,
                "returned_ids": sorted(ids),
                "total_matches": output["total_matches"],
                "truncated": output["truncated"],
                "source_checks": source_checks,
            }
        )
finally:
    data.close()
report = {
    "dataset": frozen["dataset"]["version"],
    "snapshot_time": frozen["captured_at"],
    "snapshot_sha256": hashlib.sha256(
        (ROOT / "docs/phase2/public-snapshot.json.gz").read_bytes()
    ).hexdigest(),
    "provider_calls": 0,
    "method": "PostgreSQL keyword retrieval and typed filters; labeled evidence at query limit",
    "cases": results,
    "evidence_retrieved": sum(r["evidence_retrieved"] for r in results),
    "total": len(results),
    "limits": (
        "This measures retrieval, not answer correctness or full policy coverage. "
        "Every required qualifier must be present in bounded reads of the cited source. "
        "Historical misses are retained in initial-retrieval-results.json."
    ),
}
Path(ROOT / "docs/phase2/retrieval-results.json").write_text(json.dumps(report, indent=2) + "\n")
print(
    json.dumps(
        {
            "retrieved": report["evidence_retrieved"],
            "total": report["total"],
            "misses": [r["id"] for r in results if not r["evidence_retrieved"]],
            "paid_calls": 0,
        }
    )
)

if report["evidence_retrieved"] != report["total"]:
    raise SystemExit(1)
