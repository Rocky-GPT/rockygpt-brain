"""Evidence compression preserves facts, absences, qualifiers and source identity."""

import json
from collections.abc import Callable
from copy import deepcopy
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


def timetable(*ids: str) -> dict[str, Any]:
    """A shuttle search whose code summary selects trips a (first) and d (next and last)."""
    return {
        "status": "ok",
        "records": [{"id": record_id} for record_id in ids],
        "total_matches": len(ids),
        "truncated": False,
        "schedule_calculations": {
            "status": "ok",
            "departures": [{"first": {"evidence_id": "a"}, "next": {"evidence_id": "d"},
                            "last": {"evidence_id": "d"},
                            "days": [{"first": {"evidence_id": "a"},
                                      "last": {"evidence_id": "d"}}],
                            "destinations": [{"next": {"evidence_id": "d"}}]}],
        },
    }


def test_delivery_limit_sheds_trips_the_timetable_does_not_select_first() -> None:
    # Tuesday's 30 trips were delivered as 29 and the summary went with the 30th, so the
    # checker lost the proof that 7 AM was next (09-29). The unselected trips go instead.
    from rockygpt_brain.governance.evidence import bounded_result

    source = timetable("a", "b", "c", "d", "e")
    result = bounded_result(source, lambda value: len(value["records"]) <= 3)
    # From the end, skipping the selected trip d; the rest keep their order.
    assert [record["id"] for record in result["records"]] == ["a", "b", "d"]
    summary = result["schedule_calculations"]
    assert summary["status"] == "ok" and summary["departures"] == (
        source["schedule_calculations"]["departures"])
    assert summary["delivery"] == (
        "Computed over all 5 retrieved trips. Left out of delivery: 2, none of which it "
        "selects.")
    assert result["truncated"] is True and result["reason"] == "retrieval_delivery_limit"
    assert result["retrieved_count"] == 5 and result["omitted_count"] == 2
    assert result["total_matches"] == 5 and result["status"] == "ok"
    assert "delivery" not in source["schedule_calculations"] and len(source["records"]) == 5


def test_selected_trips_always_survive_or_the_summary_goes() -> None:
    from rockygpt_brain.governance.evidence import bounded_result

    # With every unselected trip shed it still doesn't fit: the summary is dropped and
    # the records are cut from the end, as before.
    result = bounded_result(timetable("a", "b", "c", "d", "e"),
                            lambda value: len(value["records"]) <= 1)
    assert [record["id"] for record in result["records"]] == ["a"]
    assert "schedule_calculations" not in result
    assert result["truncated"] is True and result["omitted_count"] == 4


def test_a_smaller_summary_keeps_the_proof_when_the_whole_one_has_no_room() -> None:
    # Per-stop destinations made one weekday's summary three times the size of its 30
    # trips, so even with every unselected trip shed it didn't fit (09-29). A smaller form
    # that still selects the next trip does, with as many trips as there is room for.
    from rockygpt_brain.governance.evidence import bounded_result

    source = timetable("a", "b", "c", "d", "e")
    source["schedule_calculations"]["destinations"] = "x" * 500
    smaller = {"status": "ok", "departures": [{"next": {"evidence_id": "d"}}],
               "destinations_withheld": "retrieval_delivery_limit"}
    source["_bounded_summaries"] = [smaller]

    def room(size: int) -> Callable[[dict[str, Any]], bool]:
        return lambda value: len(json.dumps(value)) <= size

    whole = bounded_result(deepcopy(source), room(10_000))
    assert whole["schedule_calculations"]["destinations"] == "x" * 500
    # No room for the whole summary, room for the smaller one and every trip.
    roomy = bounded_result(deepcopy(source), room(500))
    assert [record["id"] for record in roomy["records"]] == ["a", "b", "c", "d", "e"]
    assert roomy["schedule_calculations"] == {
        **smaller, "delivery": "Computed over all 5 retrieved trips."}
    assert roomy["truncated"] is False and "_bounded_summaries" not in roomy
    # Less room: trips it doesn't select go, from the end.
    tight = bounded_result(deepcopy(source), room(425))
    assert [record["id"] for record in tight["records"]] == ["a", "d"]
    assert tight["schedule_calculations"]["delivery"] == (
        "Computed over all 5 retrieved trips. Left out of delivery: 3, none of which it "
        "selects.")
    assert tight["truncated"] is True and tight["omitted_count"] == 3
    # No room for even that: cut as before, with no summary.
    none = bounded_result(deepcopy(source), room(150))
    assert "schedule_calculations" not in none and "_bounded_summaries" not in none


def test_trips_that_have_left_are_shed_before_trips_still_to_come() -> None:
    # A Monday-night two-day lookup kept Monday's departed trips and cut Tuesday's
    # upcoming ones (09-29 final review).
    from rockygpt_brain.governance.evidence import bounded_result

    source = timetable("a", "b", "c", "d", "e")
    source["_departed_trips"] = ["b", "c"]
    result = bounded_result(source, lambda value: len(value["records"]) <= 3)
    assert [record["id"] for record in result["records"]] == ["a", "d", "e"]
    assert "_departed_trips" not in result


def test_a_smaller_summary_with_every_trip_to_come_beats_a_larger_one_without() -> None:
    # More room delivered fewer trips: the whole summary shed upcoming trips while the
    # smaller form had room for them all (09-29 final review).
    from rockygpt_brain.governance.evidence import bounded_result

    smaller = {"status": "ok", "departures": [{"next": {"evidence_id": "d"}}],
               "destinations_withheld": "retrieval_delivery_limit"}

    def room(value: dict[str, Any]) -> bool:
        whole = "destinations_withheld" not in value.get("schedule_calculations", {})
        return len(value["records"]) <= (3 if whole else 5)

    source = timetable("a", "b", "c", "d", "e")
    source["_bounded_summaries"] = [smaller]
    result = bounded_result(deepcopy(source), room)
    assert [record["id"] for record in result["records"]] == ["a", "b", "c", "d", "e"]
    assert result["schedule_calculations"]["destinations_withheld"] == (
        "retrieval_delivery_limit")
    # When the whole summary need only shed trips that have left, it stays.
    source["_departed_trips"] = ["b", "c"]
    result = bounded_result(deepcopy(source), room)
    assert [record["id"] for record in result["records"]] == ["a", "d", "e"]
    assert "destinations_withheld" not in result["schedule_calculations"]


def test_a_summary_that_proves_nothing_is_cut_like_any_result() -> None:
    from rockygpt_brain.governance.evidence import bounded_result

    source = timetable("a", "b", "c")
    source["schedule_calculations"] = {"status": "unavailable",
                                       "reason": "incomplete_schedule_coverage"}
    result = bounded_result(source, lambda value: len(value["records"]) <= 2)
    assert [record["id"] for record in result["records"]] == ["a", "b"]
    assert "schedule_calculations" not in result


def test_oversized_record_is_unavailable_not_a_false_no_match_or_partial_record() -> None:
    from rockygpt_brain.governance.evidence import bounded_result

    result = bounded_result(
        {"status": "ok", "records": [{"id": "a", "content": "qualification"}], "total_matches": 1},
        lambda value: not value["records"],
    )
    assert result["status"] == "unavailable"
    assert result["reason"] == "retrieval_delivery_limit"
    assert result["records"] == [] and result["total_matches"] == 1
