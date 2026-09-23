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
    if fits(output):
        return output
    records = output.get("records", [])
    titles = output.get("discovery_titles", [])
    limited = {**output}
    # Discovery names are navigation, not evidence. Prefer actual retrieved
    # records when both compete for the remaining context.
    if titles:
        limited["discovery_titles"] = []
        limited["discovery_titles_truncated"] = True
        limited["discovery_title_count"] = len(titles)
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
            if "entity_facts" in limited:
                limited.pop("entity_facts")
                limited["entity_facts_withheld"] = "retrieval_delivery_limit"
            if "components" in limited:
                limited.pop("components")
                limited["components_withheld"] = "retrieval_delivery_limit"
            if not count:
                limited["status"] = "unavailable"
        if fits(limited):
            if titles:
                for title_count in range(len(titles), -1, -1):
                    limited["discovery_titles"] = titles[:title_count]
                    limited["discovery_titles_truncated"] = title_count < len(titles)
                    if fits(limited):
                        break
            return limited
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
