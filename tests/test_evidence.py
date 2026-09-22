"""Evidence compression preserves facts, absences, qualifiers and source identity."""

import json
from typing import Any

from rockygpt_brain.governance.evidence import (
    compact_records,
    expand_argument_references,
    map_references,
    reference_aliases,
)


def expand_records(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
        result = dict(base)
        for key, value in changes.items():
            result[key] = (
                merge(result[key], value)
                if isinstance(result.get(key), dict) and isinstance(value, dict)
                else value
            )
        return result

    result = []
    for group in groups:
        for row in group["records"]:
            record = merge(group["defaults"], row)
            result.append(record)
    return result


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


def test_nested_defaults_preserve_missing_null_false_and_empty_values() -> None:
    cases: list[tuple[list[str] | None, dict[str, Any], str]] = [
        ([], {"vegan": False}, "published"),
        (None, {"vegan": None}, "unknown"),
        (["Milk"], {}, "unknown"),
        ([], {"vegan": 0}, "unknown"),
    ]
    records = [
        {
            "id": str(index),
            "collection": "menu",
            "url": "https://example.edu/menu",
            "fields": {"meal": "Dinner", "venue": "Hall", "allergens": allergens, **flags},
            "coverage": {"scope": "record", "fields": {"meal": "published", "vegan": coverage}},
        }
        for index, (allergens, flags, coverage) in enumerate(cases)
    ]
    packed = compact_records(records)
    assert packed[0]["defaults"]["fields"] == {"meal": "Dinner", "venue": "Hall"}
    assert packed[0]["defaults"]["coverage"]["fields"] == {"meal": "published"}
    assert json.dumps(expand_records(packed), sort_keys=True) == json.dumps(records, sort_keys=True)


def test_turn_references_are_reversible_and_do_not_rewrite_queries() -> None:
    ids = [
        "menu:00000000-0000-0000-0000-000000000001",
        "hours:00000000-0000-0000-0000-000000000002",
    ]
    aliases = reference_aliases(ids)
    reverse = {alias: record_id for record_id, alias in aliases.items()}
    record = {"id": ids[0], "fields": {"meal": "Dinner"}, "source": "https://example.edu/menu"}
    assert map_references(map_references(record, aliases), reverse) == record
    assert len(json.dumps(map_references(record, aliases))) < len(json.dumps(record))
    args = {
        "ids": [aliases[ids[0]]],
        "query": aliases[ids[0]],
        "request_text": aliases[ids[0]],
        "times": [{"evidence_id": aliases[ids[1]]}],
    }
    decoded = expand_argument_references(args, reverse)
    assert decoded["ids"] == [ids[0]]
    assert decoded["times"] == [{"evidence_id": ids[1]}]
    assert decoded["query"] == args["query"] and decoded["request_text"] == args["request_text"]
    assert (
        reference_aliases(ids + ["menu:00000000-0000-0000-0000-000000000003"])[ids[0]]
        == aliases[ids[0]]
    )


def test_shared_defaults_keep_distinct_sources_and_qualifiers_on_their_records() -> None:
    common = {"collection": "events", "freshness": "fresh", "source_title": "Campus Events"}
    records = [
        {
            **common,
            "id": "one",
            "url": "https://example.edu/one",
            "fields": {"location": "Library", "title": "Reading"},
            "limitations": ["Registration required"],
        },
        {
            **common,
            "id": "two",
            "url": "https://example.edu/two",
            "fields": {"location": "Library", "title": "Workshop"},
            "limitations": ["Students only"],
        },
    ]
    packed = compact_records(records)
    assert len(packed) == 1
    assert "url" not in packed[0]["defaults"]
    assert "limitations" not in packed[0]["defaults"]
    assert packed[0]["defaults"]["fields"] == {"location": "Library"}
    assert expand_records(packed) == records


def test_delivery_limit_never_leaves_a_schedule_summary_for_omitted_records() -> None:
    from rockygpt_brain.governance.evidence import bounded_result

    source: dict[str, Any] = {
        "status": "ok",
        "records": [{"id": "a"}, {"id": "b"}],
        "total_matches": 2,
        "truncated": False,
        "schedule_calculations": {"last_departure": {"evidence_id": "b"}},
    }
    result = bounded_result(source, lambda value: len(value["records"]) <= 1)
    assert result["records"] == [{"id": "a"}]
    assert "schedule_calculations" not in result
    assert result["truncated"] is True and result["total_matches"] == 2
    assert result["omitted_count"] == 1
    assert len(source["records"]) == 2 and "schedule_calculations" in source


def test_oversized_record_is_unavailable_not_a_false_no_match_or_partial_record() -> None:
    from rockygpt_brain.governance.evidence import bounded_result

    result = bounded_result(
        {"status": "ok", "records": [{"id": "a", "content": "qualification"}], "total_matches": 1},
        lambda value: not value["records"],
    )
    assert result["status"] == "unavailable"
    assert result["reason"] == "retrieval_delivery_limit"
    assert result["records"] == [] and result["total_matches"] == 1
