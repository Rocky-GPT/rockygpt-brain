"""Development-only browsing of original campus data and exact identity links."""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from rockygpt_brain.api.identities import _campus_data, _require_development
from rockygpt_brain.retrieval.graph import GRAPH_COLLECTIONS, GROUP_FIELDS, LABELS, GraphData
from rockygpt_brain.retrieval.projection_models import EntityProjection

router = APIRouter(prefix="/v1/dev/graph", dependencies=[Depends(_require_development)])
Version = Annotated[str | None, Query(min_length=1, max_length=160)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=10_000_000)]


def _snapshot(data: Any, dataset_version: str | None) -> dict[str, Any]:
    data._ensure_loaded()
    if dataset_version is not None and dataset_version != data.dataset["version"]:
        raise HTTPException(409, "Campus dataset changed; reload the graph")
    return {"dataset_version": data.dataset["version"], "campus_date": data.today.isoformat(),
            "timezone": "America/New_York",
            "identity_hash": data.identity_readiness().get("artifact_hash")}


def _json_arg(value: str, expected: type) -> Any:
    try:
        parsed = json.loads(value)
    except ValueError:
        raise HTTPException(422, "Invalid JSON navigation parameter") from None
    if not isinstance(parsed, expected):
        raise HTTPException(422, "Invalid JSON navigation parameter")
    return parsed


@router.get("/collections")
def collections(dataset_version: Version = None) -> dict[str, Any]:
    with _campus_data() as data:
        snapshot = _snapshot(data, dataset_version)
        entries = []
        for collection in GRAPH_COLLECTIONS:
            result = GraphData(data).browse(collection, {}, None, 0, 1)
            entries.append({
                "id": collection, "label": LABELS[collection], "total": result["total"],
                "status": "unavailable" if result["diagnostics"] else "available",
                "diagnostics": result["diagnostics"],
                "group_fields": [{"key": key, "label": key.replace("_", " ").title()}
                                 for key in GROUP_FIELDS[collection]],
            })
        return {**snapshot, "collections": entries}


@router.get("/browse")
def browse(
    collection: Annotated[str, Query(min_length=1, max_length=80)],
    entity_id: UUID | None = None,
    group_by: Annotated[str | None, Query(max_length=80)] = None,
    filters: Annotated[str, Query(max_length=4000)] = "{}",
    offset: Offset = 0, limit: Limit = 24, dataset_version: Version = None,
) -> dict[str, Any]:
    selection = _json_arg(filters, dict)
    GraphData.validate(collection, selection, group_by)
    with _campus_data() as data:
        snapshot = _snapshot(data, dataset_version)
        graph = GraphData(data, entity_id)
        return {**snapshot, "collection": collection,
                "scope": {"entity_id": str(entity_id) if entity_id else None,
                          "filters": selection, "group_by": group_by},
                **graph.browse(collection, selection, group_by, offset, limit)}


@router.get("/record")
def record(
    collection: Annotated[str, Query(min_length=1, max_length=80)],
    record_id: Annotated[str | None, Query(min_length=1, max_length=1200)] = None,
    source_key: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    source_record_key: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    source_record_id: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    entity_id: UUID | None = None, dataset_version: Version = None,
) -> dict[str, Any]:
    GraphData.validate(collection, {})
    if record_id is None and (source_key is None or source_record_key is None):
        raise HTTPException(422, "Supply record_id or an exact source reference")
    if record_id is not None and any((source_key, source_record_key, source_record_id)):
        raise HTTPException(422, "Supply one record selector")
    with _campus_data() as data:
        snapshot = _snapshot(data, dataset_version)
        graph = GraphData(data, entity_id)
        value = (graph.record(collection, record_id) if record_id is not None else
                 graph.reference(collection, str(source_key), str(source_record_key),
                                 source_record_id))
        return {**snapshot, "record": value, "diagnostics": graph.diagnostics}


@router.get("/value")
def artifact_value(
    collection: Annotated[str, Query(min_length=1, max_length=80)],
    record_id: Annotated[str, Query(min_length=1, max_length=240)],
    path: Annotated[str, Query(max_length=4000)] = "[]",
    offset: Offset = 0, limit: Limit = 24, dataset_version: Version = None,
) -> dict[str, Any]:
    if collection != "artifacts" or not record_id.startswith("artifacts:"):
        raise HTTPException(422, "Value navigation requires an artifact record ID")
    artifact_key = record_id[len("artifacts:"):]
    segments = _json_arg(path, list)
    if len(segments) > 100 or any(
        isinstance(key, bool) or not isinstance(key, (str, int)) or len(str(key)) > 500
        for key in segments
    ):
        raise HTTPException(422, "Invalid artifact path")
    with _campus_data() as data:
        snapshot = _snapshot(data, dataset_version)
        return {**snapshot, **GraphData(data).artifact_value(
            artifact_key, [str(key) for key in segments], offset, limit)}


@router.get("/knowledge")
def knowledge(dataset_version: Version = None) -> dict[str, Any]:
    from rockygpt_brain.retrieval.knowledge import release_graph

    with _campus_data() as data:
        snapshot = _snapshot(data, dataset_version)
        return {**snapshot, **release_graph(data).index}


@router.get("/export")
def graph_export() -> JSONResponse:
    from rockygpt_brain.retrieval.graph_export import export_graph

    with _campus_data() as data:
        data._ensure_loaded()
        assert data.connection is not None
        # Pin every graph/registry/metadata read to one database snapshot. The
        # initial active-release lookup is rechecked by export_graph in this transaction.
        with data.connection.transaction():
            data.connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            snapshot = _snapshot(data, None)
            result = export_graph(data, snapshot)
        return JSONResponse(result, headers={
            "Content-Disposition": 'attachment; filename="campus-knowledge-graph.json"',
            "Cache-Control": "no-store",
        })


@router.get("/projection/v2")
def projection_v2(
    entity_id: UUID,
    dataset_version: Annotated[str, Query(min_length=1, max_length=160)],
    identity_hash: Annotated[str, Query(min_length=1, max_length=128)],
    record_group: Annotated[str | None, Query(max_length=80)] = None,
    filters: Annotated[str, Query(max_length=2000)] = "{}",
    limit: Limit = 8,
    cursor: Annotated[str | None, Query(max_length=4000)] = None,
) -> EntityProjection:
    from rockygpt_brain.retrieval.projection import Projection, validate_selection

    selection = _json_arg(filters, dict)
    validate_selection(record_group, selection, cursor)
    with _campus_data() as data:
        snapshot = _snapshot(data, dataset_version)
        if snapshot["identity_hash"] != identity_hash:
            raise HTTPException(409, "Campus identity links changed; reload the projection")
        return Projection(data, snapshot).build(entity_id, record_group, selection, limit, cursor)
