"""Lossless wire representation of records with recursively shared defaults."""

import json
from itertools import groupby
from typing import Any


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
