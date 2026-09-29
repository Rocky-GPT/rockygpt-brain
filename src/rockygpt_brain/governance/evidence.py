"""Lossless wire representation of records with recursively shared defaults."""

import json
from collections.abc import Callable
from itertools import groupby
from typing import Any


def bounded_result(
    output: dict[str, Any], fits: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """Bound NEW delivery only; retain complete records and explicit set coverage.

    Never alter an individual record's facts or silently treat an omitted result
    as absent. A caller's prior evidence/history is outside this function.
    """
    # The records a placement merged into, as they were before; never delivered.
    unplaced = output.get("_unplaced_records") or {}
    # Smaller forms of a timetable's summary (campus/schedules.bounded_summaries), largest
    # first, for when the whole one has no room, and the trips that have already left.
    smaller = output.get("_bounded_summaries") or []
    departed = set(output.get("_departed_trips") or ())
    output = {key: value for key, value in output.items()
              if key not in {"_unplaced_records", "_bounded_summaries", "_departed_trips"}}
    if fits(output):
        return output
    if output.get("placement"):
        # A placement is an addition: shed it, the records only it cites and what it
        # merged into the others, before anything the rest of the result relies on.
        cited = {identifier for item in output["placement"]
                 for identifier in [*item.get("evidence_ids", []),
                                    *item.get("target_evidence_ids", [])]}
        supporting = {identifier
                      for prop in (output.get("entity_facts") or {}).get("properties", [])
                      for value in prop.get("values", [])
                      for identifier in value.get("supporting_evidence_ids", [])}
        shed = {key: value for key, value in output.items() if key != "placement"}
        shed["records"] = [unplaced.get(record["id"], record)
                           for record in output.get("records", [])
                           if record["id"] not in cited - supporting]
        output = {**shed, "placement_withheld": "retrieval_delivery_limit"}
        # Without room for the marker, the result as it was before the placement.
        for candidate in (output, shed):
            if fits(candidate):
                return candidate
    records = output.get("records", [])
    titles = output.get("discovery_titles", [])
    limited = {**output}
    # Discovery names are navigation, not evidence. Prefer actual retrieved
    # records when both compete for the remaining context.
    if titles:
        limited["discovery_titles"] = []
        limited["discovery_titles_truncated"] = True
        limited["discovery_title_count"] = len(titles)

    def with_titles(candidate: dict[str, Any]) -> dict[str, Any]:
        if titles:
            for title_count in range(len(titles), -1, -1):
                candidate["discovery_titles"] = titles[:title_count]
                candidate["discovery_titles_truncated"] = title_count < len(titles)
                if fits(candidate):
                    break
        return candidate

    summary = output.get("schedule_calculations")
    if isinstance(summary, dict) and summary.get("status") == "ok":
        timetable = bounded_timetable(limited, records, [summary, *smaller], departed, fits)
        if timetable is not None:
            return with_titles(timetable)
    for count in range(len(records), -1, -1):
        if count < len(records):
            limited.update(
                records=records[:count],
                truncated=True,
                reason="retrieval_delivery_limit",
                retrieved_count=len(records),
                omitted_count=len(records) - count,
            )
            # Derived summaries must not claim coverage of omitted evidence.
            limited.pop("schedule_calculations", None)
            derived_withheld(limited)
            if not count:
                limited["status"] = "unavailable"
        if fits(limited):
            return with_titles(limited)
    return {
        "status": "unavailable",
        "reason": "retrieval_delivery_limit",
        "dataset_version": output.get("dataset_version"),
        "records": [],
        "truncated": True,
        "total_matches": output.get("total_matches"),
        "retrieved_count": len(records),
        "omitted_count": len(records),
    }


def bounded_timetable(
    limited: dict[str, Any],
    records: list[dict[str, Any]],
    forms: list[dict[str, Any]],
    departed: set[str],
    fits: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    """A timetable whose summary still proves first, next and last, or None if none fits.

    The summary was worked out by code over every retrieved trip. Dropping it with the
    first trip cut lost Tuesday's "next is 7 AM" proof for one unselected 9:40 PM trip, and
    the checker rejected the answer (09-29). Only trips the summary doesn't select are
    shed, those that have left first, then from the end. The largest form of the summary
    that keeps every trip still to come wins; failing that, the largest that fits at all.
    """

    def scheduled(form: dict[str, Any], shed: list[int]) -> dict[str, Any]:
        note = f"Computed over all {len(records)} retrieved trips."
        if not shed:
            return {**limited, "schedule_calculations": {**form, "delivery": note}}
        return derived_withheld({
            **limited,
            "records": [record for index, record in enumerate(records) if index not in shed],
            "truncated": True,
            "reason": "retrieval_delivery_limit",
            "retrieved_count": len(records),
            "omitted_count": len(shed),
            "schedule_calculations": {
                **form,
                "delivery": f"{note} Left out of delivery: {len(shed)}, none of which it "
                "selects.",
            },
        })

    fallback: dict[str, Any] | None = None
    for form in forms:
        selected = set(summary_references(form))
        unselected = sorted(
            (index for index in reversed(range(len(records)))
             if records[index].get("id") not in selected),
            key=lambda index: records[index].get("id") not in departed)
        if not fits(scheduled(form, unselected)):
            continue
        # Shedding another trip never makes the result bigger, so search for the fewest to
        # shed. The whole summary with every trip was already tried.
        low, high = int(form is forms[0]), len(unselected)
        while low < high:
            middle = (low + high) // 2
            if fits(scheduled(form, unselected[:middle])):
                high = middle
            else:
                low = middle + 1
        result = scheduled(form, unselected[:high])
        if all(records[index].get("id") in departed for index in unselected[:high]):
            return result
        fallback = fallback or result
    return fallback


def derived_withheld(limited: dict[str, Any]) -> dict[str, Any]:
    """Withhold the summaries built over a result's records once some are left out."""
    for key in ("entity_facts", "components", "placement"):
        if key in limited:
            limited.pop(key)
            limited[f"{key}_withheld"] = "retrieval_delivery_limit"
    return limited


def summary_references(value: Any) -> list[str]:
    """Every trip a timetable summary selects, wherever it names one by evidence_id."""
    if isinstance(value, dict):
        own = [value["evidence_id"]] if isinstance(value.get("evidence_id"), str) else []
        return [*own, *(found for item in value.values() for found in summary_references(item))]
    if isinstance(value, list):
        return [found for item in value for found in summary_references(item)]
    return []


def tool_result_wire(
    output: dict[str, Any], sent: dict[str, dict[str, Any]], evidence_ids: list[str]
) -> str:
    """Encode without mutating delivery state, also usable for context admission."""
    wire_output = dict(output)
    records = wire_output.pop("records", None)
    if records is not None:
        wire_output["evidence_groups"] = compact_records(
            [record for record in records if sent.get(record["id"]) != record]
        )
        repeated = [record["id"] for record in records if sent.get(record["id"]) == record]
        if repeated:
            wire_output["unchanged_evidence_ids"] = repeated
    return json.dumps(
        map_references(wire_output, reference_aliases(evidence_ids)),
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def common_values(rows: list[dict[str, Any]], *, root: bool = False) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for key, value in rows[0].items():
        if (root and key in {"id", "entity_id", "title"}) or not all(key in row for row in rows):
            continue
        values = [row[key] for row in rows]
        if all(
            json.dumps(item, sort_keys=True, default=str)
            == json.dumps(value, sort_keys=True, default=str)
            for item in values
        ):
            defaults[key] = value
        elif all(isinstance(item, dict) for item in values):
            nested = common_values(values)
            if nested:
                defaults[key] = nested
    return defaults


def without_defaults(row: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key not in defaults:
            result[key] = value
        elif isinstance(value, dict) and isinstance(defaults[key], dict):
            remainder = without_defaults(value, defaults[key])
            if remainder:
                result[key] = remainder
    return result


def compact_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct by recursively merging each record over its group's defaults.

    Shared defaults exist only for keys present in every record with the same
    JSON type/value. Missing, null, false and empty remain distinct. Arrays are
    values, never merged. Identifiers stay on each row; sources never conflate.
    """

    groups: list[dict[str, Any]] = []
    for _, adjacent in groupby(records, key=lambda r: r.get("collection")):
        rows = list(adjacent)
        defaults = common_values(rows, root=True) if len(rows) > 1 else {}
        groups.append(
            {
                "defaults": defaults,
                "records": [without_defaults(row, defaults) for row in rows],
            }
        )
    return groups


def reference_aliases(ids: list[str]) -> dict[str, str]:
    """Turn-local opaque names reduce repeated UUID tokens, never source content."""
    return {value: f"record_{index}" for index, value in enumerate(ids, 1) if len(value) > 24}


def map_references(value: Any, aliases: dict[str, str]) -> Any:
    """Reversible exact identifier substitution in evidence payloads, not conversation."""
    if isinstance(value, str):
        return aliases.get(value, value)
    if isinstance(value, list):
        return [map_references(item, aliases) for item in value]
    if isinstance(value, dict):
        return {aliases.get(key, key): map_references(item, aliases) for key, item in value.items()}
    return value


def expand_argument_references(value: Any, aliases: dict[str, str]) -> Any:
    """Only declared reference fields expand; search text and user quotes stay literal."""
    if isinstance(value, list):
        return [expand_argument_references(item, aliases) for item in value]
    if isinstance(value, dict):
        return {
            key: map_references(item, aliases)
            if key in {"ids", "evidence_id", "evidence_ids"}
            else expand_argument_references(item, aliases)
            for key, item in value.items()
        }
    return value
