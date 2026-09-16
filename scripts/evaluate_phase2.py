"""Independent keyword/filter evaluation. No provider or model client is imported."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from phase2_snapshot import ROOT, local_database, snapshot

from rockygpt_brain.data import CampusData, SearchQuery

frozen = snapshot()
cases = json.loads((ROOT / "docs/phase2/retrieval-cases.json").read_text())
data = CampusData(local_database(), datetime.fromisoformat(frozen["captured_at"]))
results = []
try:
    for case in cases:
        output = data.search(SearchQuery.model_validate(case["query"]))
        records = output["records"]
        ids = {r["id"] for r in records}
        passed = bool(records)
        if "expected_entity" in case:
            passed = any(r["entity_id"] == case["expected_entity"] for r in records)
        if "expected_ids" in case:
            passed = bool(ids.intersection(case["expected_ids"]))
        if case.get("expect_empty"):
            passed = not records
        for key, value in case.get("every_field", {}).items():
            passed = passed and all(r["fields"].get(key) == value for r in records)
        if case.get("every_date"):
            passed = passed and all(r["valid_from"] == case["every_date"] for r in records)
        results.append(
            {
                "id": case["id"],
                "evidence_retrieved": passed,
                "returned_ids": sorted(ids),
                "total_matches": output["total_matches"],
                "truncated": output["truncated"],
                "source_gap": case.get("source_gap"),
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
        "Known paraphrase/source gaps are retained."
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
