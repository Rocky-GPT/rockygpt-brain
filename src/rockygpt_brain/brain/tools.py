"""Model-callable tools, one per rockygpt-data search/lookup endpoint.

Arguments the model supplies are never trusted at face value: `execute_tool`
re-validates every call against the same JSON-schema `properties` used to
advertise the tool to the model (exact allowed keys, string type, length
bound, enum membership — see `_validate_arguments`), rejecting anything else
with a small, fixed, non-reflective error rather than passing attacker- or
model-controlled values straight to the data client.

Only records that survive both the per-call cap (`MAX_RECORDS_PER_CALL`) and
the final total-size cap (`MAX_TOTAL_SERIALIZED_BYTES`, measured on the
*complete* returned tool object — dataset fields and all — as UTF-8 bytes of
its JSON serialization) have their `source` recorded into the turn's
`ProvenanceRegistry`. Sizing and dropping happens strictly before
registration, so a record trimmed for size is never citable even though it
was briefly present in an intermediate, unsent representation (DESIGN.md
§4). Every field is also recursively bounded (string length, list length,
nesting depth, non-finite floats rejected) before that size check runs, so
a large or malformed data-service response cannot exhaust model context or
produce invalid JSON.

`at` (the pinned-time parameter accepted by rockygpt-data's hours/shuttle/
menu endpoints) is never a model-controlled parameter: it is always injected
server-side from the turn's `TimeContext` (spec/acceptance.md: "Pinned now
and timezone values control hours and shuttle calculations").
"""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from rockygpt_brain.brain.grounding import ProvenanceRegistry
from rockygpt_brain.brain.time_context import TimeContext
from rockygpt_brain.data_client.client import DataServiceClient
from rockygpt_brain.data_client.errors import DataContractError
from rockygpt_brain.data_client.models import SearchResult, Source, normalize_source

MAX_RECORDS_PER_CALL = 8
MAX_STRING_LENGTH = 500
MAX_LIST_ITEMS = 20
MAX_DEPTH = 4
MAX_TOTAL_SERIALIZED_BYTES = 8_000

_DAY_ENUM = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_SERVICE_DAY_ENUM = ["weekday", "saturday", "sunday"]
# Namespaces brain-minted map source ids away from data-service ids.
_MAP_SOURCE_ID_PREFIX = "map:"


@dataclass(frozen=True, slots=True)
class ToolPayload:
    """What a tool handler returns, before summarizing.

    Most endpoints answer with the dataset-versioned `SearchResult`
    envelope, but `/v1/map` does not — it returns bare locations with no
    dataset and no per-record `source`. This is the shape both can take,
    so `_summarize` stays a single code path rather than growing a
    per-endpoint special case.
    """

    records: list[dict[str, Any]]
    dataset_id: str | None = None
    dataset_version: str | None = None

    @classmethod
    def from_search(cls, result: SearchResult) -> ToolPayload:
        return cls(
            records=result.records,
            dataset_id=result.dataset.id,
            dataset_version=result.dataset.version,
        )


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[DataServiceClient, TimeContext, dict[str, Any]], Awaitable[ToolPayload]]


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


def _summarize(result: ToolPayload, *, registry: ProvenanceRegistry) -> dict[str, Any]:
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


def _validate_arguments(tool: ToolDefinition, arguments: Any) -> dict[str, str] | None:
    """Re-validate model-supplied arguments against the tool's own schema.

    The JSON schema in `tool.parameters` is advertised to the model, but the
    model's output is never trusted at face value: this enforces the same
    bounds (object type, exact allowed keys, string type, length, enum)
    server-side before anything reaches the data client.
    """
    if not isinstance(arguments, dict):
        return None
    properties: dict[str, Any] = tool.parameters.get("properties", {})
    if not set(arguments.keys()) <= set(properties.keys()):
        return None
    validated: dict[str, str] = {}
    for key, value in arguments.items():
        spec = properties[key]
        if spec.get("type") != "string" or not isinstance(value, str):
            return None
        max_length = spec.get("maxLength")
        if max_length is not None and len(value) > max_length:
            return None
        enum = spec.get("enum")
        if enum is not None and value not in enum:
            return None
        validated[key] = value
    return validated


async def _search_campus_hours(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(await client.search_campus_hours(
        q=args.get("q"), day=args.get("day"), at=time_context.as_at_param()
    ))


async def _search_dining_hours(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(await client.search_dining_hours(
        q=args.get("q"), day=args.get("day"), at=time_context.as_at_param()
    ))


async def _search_menu(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(await client.search_menu(
        q=args.get("q"), meal=args.get("meal"), at=time_context.as_at_param()
    ))


async def _search_contacts(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(
        await client.search_contacts(q=args.get("q"), at=time_context.as_at_param())
    )


async def _search_clubs(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(
        await client.search_clubs(q=args.get("q"), at=time_context.as_at_param())
    )


async def _search_events(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(
        await client.search_events(q=args.get("q"), at=time_context.as_at_param())
    )


async def _search_programs(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(
        await client.search_programs(q=args.get("q"), at=time_context.as_at_param())
    )


async def _search_academic_dates(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(
        await client.search_academic_dates(q=args.get("q"), at=time_context.as_at_param())
    )


async def _search_shuttles(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(await client.search_shuttles(
        route=args.get("route"),
        service_day=args.get("serviceDay"),
        at=time_context.as_at_param(),
    ))


async def _search_map(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    response = await client.map(q=args.get("q"))
    locations = response.get("locations")
    if not isinstance(locations, list):
        raise DataContractError("Map response locations must be an array.")

    # `/v1/map` does not filter `locations` by `q` — it returns the entire
    # campus in a fixed order and reports the query's best match separately
    # in `resolved`. Left alone, MAX_RECORDS_PER_CALL would then hand the
    # model the same arbitrary first few buildings for every question and
    # drop the one it actually asked about. Leading with `resolved` is what
    # makes this tool answer "where is X" at all.
    resolved = response.get("resolved")
    ordered: list[Any] = []
    resolved_key: str | None = None
    if isinstance(resolved, dict):
        ordered.append(resolved)
        key = resolved.get("key")
        resolved_key = key if isinstance(key, str) else None
    ordered.extend(locations)

    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for location in ordered:
        if not isinstance(location, dict):
            continue
        location_key = location.get("key")
        if isinstance(location_key, str):
            if location_key in seen_keys:
                continue
            seen_keys.add(location_key)
        if isinstance(location_key, str) and location_key == resolved_key:
            location = {**location, "bestMatch": True}
        record = dict(location)
        source = _map_source(location)
        if source is not None:
            # `_summarize` reads provenance from a record's "source" key and
            # is the only thing that registers it, so a location that cannot
            # produce a valid source stays in the result but is not citable
            # — the same rule every other tool follows.
            record["source"] = {
                "sourceId": source.source_id,
                "title": source.title,
                "url": source.url,
            }
        records.append(record)
    return ToolPayload(records=records)


def _map_source(location: dict[str, Any]) -> Source | None:
    """Mint a citable source for one campus location.

    `/v1/map` is the one campus endpoint that returns no `source` of its
    own, so without this its locations could never be cited and every map
    answer would have to route "ungrounded". The provenance guarantee is
    unchanged: the model still cannot author a citation, it can only select
    an id the brain derived — here from a `key` the data service returned
    this turn, with the title and URL taken from that same record. The
    `map:` prefix keeps these distinguishable from data-service-issued
    source ids.
    """
    key = location.get("key")
    name = location.get("name")
    map_url = location.get("mapUrl")
    if not isinstance(key, str) or not isinstance(name, str) or not isinstance(map_url, str):
        return None
    if not key.strip() or not name.strip() or not map_url.strip():
        return None
    return normalize_source(
        Source(source_id=f"{_MAP_SOURCE_ID_PREFIX}{key}", title=name, url=map_url)
    )


TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="search_campus_hours",
        description="Search official campus facility hours (offices, libraries, gyms, etc).",
        parameters={
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Facility name/keywords.",
                },
                "day": {"type": "string", "enum": _DAY_ENUM},
            },
            "additionalProperties": False,
        },
        handler=_search_campus_hours,
    ),
    ToolDefinition(
        name="search_dining_hours",
        description="Search dining hall / cafe hours.",
        parameters={
            "type": "object",
            "properties": {
                "q": {"type": "string", "maxLength": 200},
                "day": {"type": "string", "enum": _DAY_ENUM},
            },
            "additionalProperties": False,
        },
        handler=_search_dining_hours,
    ),
    ToolDefinition(
        name="search_menu",
        description="Search structured dining menu items.",
        parameters={
            "type": "object",
            "properties": {
                "q": {"type": "string", "maxLength": 200},
                "meal": {"type": "string", "maxLength": 64},
            },
            "additionalProperties": False,
        },
        handler=_search_menu,
    ),
    ToolDefinition(
        name="search_contacts",
        description="Search campus office and staff/faculty contacts.",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=_search_contacts,
    ),
    ToolDefinition(
        name="search_clubs",
        description="Search student clubs and organizations.",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=_search_clubs,
    ),
    ToolDefinition(
        name="search_events",
        description="Search campus events.",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=_search_events,
    ),
    ToolDefinition(
        name="search_programs",
        description="Search academic programs (majors, minors, certificates).",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=_search_programs,
    ),
    ToolDefinition(
        name="search_academic_dates",
        description="Search academic calendar dates (breaks, deadlines, terms).",
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=_search_academic_dates,
    ),
    ToolDefinition(
        name="search_shuttles",
        description="Search shuttle/train-loop/Shortline trips.",
        parameters={
            "type": "object",
            "properties": {
                "route": {"type": "string", "maxLength": 120},
                "serviceDay": {"type": "string", "enum": _SERVICE_DAY_ENUM},
            },
            "additionalProperties": False,
        },
        handler=_search_shuttles,
    ),
    ToolDefinition(
        name="search_map",
        description=(
            "Find campus buildings, offices, parking, and room locations. "
            "Use for any 'where is X' or 'how do I get to X' question. Each "
            "result's `key` is the locationKey for a VIEW_MAP uiAction."
        ),
        parameters={
            "type": "object",
            "properties": {"q": {"type": "string", "maxLength": 200}},
            "additionalProperties": False,
        },
        handler=_search_map,
    ),
]

TOOL_HANDLERS: dict[str, ToolDefinition] = {tool.name: tool for tool in TOOL_DEFINITIONS}


def openai_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in TOOL_DEFINITIONS
    ]


async def execute_tool(
    name: str,
    arguments: Any,
    *,
    client: DataServiceClient,
    time_context: TimeContext,
    registry: ProvenanceRegistry,
) -> dict[str, Any]:
    tool = TOOL_HANDLERS.get(name)
    if tool is None:
        return {"error": "unknown_tool"}
    validated_arguments = _validate_arguments(tool, arguments)
    if validated_arguments is None:
        return {"error": "invalid_arguments"}
    result = await tool.handler(client, time_context, validated_arguments)
    return _summarize(result, registry=registry)
