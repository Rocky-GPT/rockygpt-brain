"""Lossless wire representation of retrieved records, without repeated metadata."""

import json
from itertools import groupby
from typing import Any


def compact_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Factor equal top-level values within adjacent source/collection groups.

    A record is exactly {**group['defaults'], **row}. Never shorten field values,
    discard records, conflate null with absent, or merge records across sources.
    Keep identifying fields on each row even when every record has the same value.
    """
    groups: list[dict[str, Any]] = []
    for _, adjacent in groupby(records, key=lambda r: (r.get("collection"), r.get("url"))):
        rows = list(adjacent)
        defaults = {
            key: value
            for key, value in rows[0].items()
            if key not in {"id", "entity_id", "title"}
            and len(rows) > 1
            and all(
                key in row
                and json.dumps(row[key], sort_keys=True, default=str)
                == json.dumps(value, sort_keys=True, default=str)
                for row in rows[1:]
            )
        }
        groups.append(
            {
                "defaults": defaults,
                "records": [
                    {key: value for key, value in row.items() if key not in defaults}
                    for row in rows
                ],
            }
        )
    return groups
