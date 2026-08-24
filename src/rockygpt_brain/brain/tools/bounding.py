"""Bounding and summarizing a tool result before the model ever sees it.

Every field is recursively bounded (string length, list length, nesting
depth, non-finite floats rejected) and the whole envelope is then trimmed to
`MAX_TOTAL_SERIALIZED_BYTES`, so a large or malformed data-service response
cannot exhaust model context or produce invalid JSON.

Sizing and dropping happens strictly before provenance registration, so a
record trimmed for size is never citable even though it was briefly present
in an intermediate, unsent representation (DESIGN.md §4).
"""

from __future__ import annotations

import json
import math
from typing import Any

from rockygpt_brain.brain.grounding import ProvenanceRegistry
from rockygpt_brain.brain.tools.payload import ToolPayload
from rockygpt_brain.data_client.errors import DataContractError
from rockygpt_brain.data_client.models import Source, normalize_source

MAX_RECORDS_PER_CALL = 8
MAX_STRING_LENGTH = 500
MAX_LIST_ITEMS = 20
MAX_DEPTH = 4
MAX_TOTAL_SERIALIZED_BYTES = 8_000


def _bound_value(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return None
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value[:MAX_STRING_LENGTH]
    if isinstance(value, list):
        return [_bound_value(item, depth=depth + 1) for item in value[:MAX_LIST_ITEMS]]
    if isinstance(value, dict):
        bounded: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_LIST_ITEMS]:
            bounded[str(key)[:100]] = _bound_value(item, depth=depth + 1)
        return bounded
    return str(value)[:MAX_STRING_LENGTH]


def _serialized_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def summarize(result: ToolPayload, *, registry: ProvenanceRegistry) -> dict[str, Any]:
    truncated_records = result.records[:MAX_RECORDS_PER_CALL]
    entries: list[tuple[dict[str, Any], Source | None]] = []
    for record in truncated_records:
        summary = {key: value for key, value in record.items() if key != "source"}
        source_data = record.get("source")
        source: Source | None = None
        if isinstance(source_data, dict):
            try:
                raw_source = Source.from_json(source_data)
            except DataContractError:
                raw_source = None
            if raw_source is not None:
                # The same normalization grounding.ProvenanceRegistry.record
                # applies, so the sourceId exposed to the model here is
                # exactly the id it will be citable under.
                source = normalize_source(raw_source)
        if source is not None:
            summary["sourceId"] = source.source_id
        entries.append((_bound_value(summary), source))

    envelope: dict[str, Any] = {
        "recordCount": len(result.records),
        "records": [record for record, _source in entries],
    }
    # Omitted entirely for endpoints that carry no dataset version, rather
    # than sent as null — an explicit null reads to the model as "this data
    # has no version", which is a different claim from "this endpoint does
    # not version its data".
    if result.dataset_id is not None:
        envelope["datasetId"] = _bound_value(result.dataset_id)
    if result.dataset_version is not None:
        envelope["datasetVersion"] = _bound_value(result.dataset_version)
    while entries and _serialized_size(envelope) > MAX_TOTAL_SERIALIZED_BYTES:
        entries.pop()
        envelope["records"] = [record for record, _source in entries]

    # Only sources belonging to entries that survived every trim above are
    # ever recorded — a record cut for count or size is never citable.
    registry.record([source for _record, source in entries if source is not None])
    return envelope
