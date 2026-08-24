"""One handler per rockygpt-data search/lookup endpoint.

Each handler is a thin adapter: it maps validated arguments onto a data
client call and wraps the reply in a `ToolPayload`. The one exception is
`search_map`, which has to reorder and mint provenance for an endpoint that
supplies neither — see the comments there.

`at` (the pinned-time parameter accepted by rockygpt-data's hours/shuttle/
menu endpoints) is never a model-controlled parameter: it is always injected
server-side from the turn's `TimeContext` (spec/acceptance.md: "Pinned now
and timezone values control hours and shuttle calculations").
"""

from __future__ import annotations

from typing import Any

from rockygpt_brain.brain.time_context import TimeContext
from rockygpt_brain.brain.tools.payload import ToolPayload
from rockygpt_brain.data_client.client import DataServiceClient
from rockygpt_brain.data_client.errors import DataContractError
from rockygpt_brain.data_client.models import Source, normalize_source

# Namespaces brain-minted map source ids away from data-service ids.
_MAP_SOURCE_ID_PREFIX = "map:"


async def search_campus_hours(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(await client.search_campus_hours(
        q=args.get("q"), day=args.get("day"), at=time_context.as_at_param()
    ))


async def search_dining_hours(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(await client.search_dining_hours(
        q=args.get("q"), day=args.get("day"), at=time_context.as_at_param()
    ))


async def search_menu(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(await client.search_menu(
        q=args.get("q"), meal=args.get("meal"), at=time_context.as_at_param()
    ))


async def search_contacts(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(
        await client.search_contacts(q=args.get("q"), at=time_context.as_at_param())
    )


async def search_clubs(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(
        await client.search_clubs(q=args.get("q"), at=time_context.as_at_param())
    )


async def search_events(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(
        await client.search_events(q=args.get("q"), at=time_context.as_at_param())
    )


async def search_programs(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(
        await client.search_programs(q=args.get("q"), at=time_context.as_at_param())
    )


async def search_academic_dates(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(
        await client.search_academic_dates(q=args.get("q"), at=time_context.as_at_param())
    )


async def search_shuttles(
    client: DataServiceClient, time_context: TimeContext, args: dict[str, Any]
) -> ToolPayload:
    return ToolPayload.from_search(await client.search_shuttles(
        route=args.get("route"),
        service_day=args.get("serviceDay"),
        at=time_context.as_at_param(),
    ))


async def search_map(
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
            # `bounding.summarize` reads provenance from a record's "source"
            # key and is the only thing that registers it, so a location that
            # cannot produce a valid source stays in the result but is not
            # citable — the same rule every other tool follows.
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
