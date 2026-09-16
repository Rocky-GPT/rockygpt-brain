"""Evidence compression preserves facts, absences, qualifiers and source identity."""

import json
from typing import Any

from rockygpt_brain.evidence import compact_records


def expand_records(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**group["defaults"], **row} for group in groups for row in group["records"]]


def test_compaction_is_lossless_for_mixed_sources_and_partial_coverage() -> None:
    common = {
        "collection": "menu",
        "url": "https://example.edu/menu",
        "freshness": "fresh",
        "limitations": ["Labels do not establish allergy safety."],
    }
    records: list[dict[str, Any]] = [
        {**common, "id": "a", "fields": {"vegan": True, "allergens": []}},
        {**common, "id": "b", "fields": {"vegan": None, "allergens": ["Milk"]}},
        {**common, "id": "c", "fields": {"allergens": []}, "content_truncated": True},
        {**common, "id": "d", "url": "https://example.edu/other", "freshness": "stale"},
        {**common, "id": "e", "fields": {"vegan": False}},
        {**common, "id": "f", "fields": {"vegan": 0}},
    ]
    before = json.dumps(records, sort_keys=True)
    packed = json.loads(json.dumps(compact_records(records)))
    assert json.dumps(expand_records(packed), sort_keys=True) == before
    assert json.dumps(records, sort_keys=True) == before  # Inputs are not mutated.
    assert len(json.dumps(packed)) < len(before)
    assert compact_records([]) == []
