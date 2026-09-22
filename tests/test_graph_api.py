"""Campus Graph inspects exact published records, without paid retrieval or mutation."""

from __future__ import annotations

import copy
import json
import os
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.retrieval.graph import GRAPH_COLLECTIONS, GraphData, _page
from test_profiles import ENTITY_ID, NOW, repository


@pytest.fixture(autouse=True)
def development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")


@pytest.mark.parametrize("environment", [None, "production", "staging", "Development"])
@pytest.mark.parametrize("path", ["collections", "browse", "record", "value"])
def test_development_routes_are_hidden_before_parameter_validation(
    monkeypatch: pytest.MonkeyPatch, environment: str | None, path: str,
) -> None:
    if environment is None:
        monkeypatch.delenv("BRAIN_ENVIRONMENT")
    else:
        monkeypatch.setenv("BRAIN_ENVIRONMENT", environment)
    with patch("rockygpt_brain.api.identities.CampusData") as factory:
        response = TestClient(app).get(f"/v1/dev/graph/{path}")
    assert response.status_code == 404
    factory.assert_not_called()


@pytest.mark.parametrize("path,params", [
    ("collections", {}), ("browse", {"collection": "menu"}),
    ("record", {"collection": "events", "record_id": "events:one"}),
    ("value", {"collection": "artifacts", "record_id": "artifacts:menu"}),
])
def test_release_mismatch_blocks_every_child_request_before_record_read(
    path: str, params: dict[str, str],
) -> None:
    data = repository()
    data._fetch = Mock(side_effect=AssertionError("Must not read another release"))
    with patch("rockygpt_brain.api.identities.CampusData", return_value=data):
        response = TestClient(app).get(f"/v1/dev/graph/{path}",
                                      params={**params, "dataset_version": "old"})
    assert response.status_code == 409
    data._fetch.assert_not_called()


@pytest.mark.parametrize("path,params", [
    ("browse", {"collection": "unknown"}),
    ("browse", {"collection": "menu", "group_by": "untrusted_sql"}),
    ("browse", {"collection": "menu", "filters": "broken"}),
    ("browse", {"collection": "menu", "filters": "[]"}),
    ("browse", {"collection": "menu", "filters": '{"meal":[]}' }),
    ("browse", {"collection": "menu", "limit": 101}),
    ("browse", {"collection": "menu", "offset": -1}),
    ("browse", {"collection": "artifacts", "group_by": "source_key"}),
    ("record", {"collection": "events"}),
    ("record", {"collection": "events", "record_id": "events:a", "source_key": "x"}),
    ("value", {"collection": "menu", "record_id": "menu:x"}),
    ("value", {"collection": "artifacts", "record_id": "artifacts:x", "path": '[true]'}),
])
def test_invalid_navigation_does_not_connect(path: str, params: dict[str, Any]) -> None:
    with patch("rockygpt_brain.api.identities.CampusData") as factory:
        response = TestClient(app).get(f"/v1/dev/graph/{path}", params=params)
    assert response.status_code == 422
    factory.assert_not_called()


def test_catalogue_enumerates_all_collections_and_no_model_calls() -> None:
    data = repository()
    with (
        patch("rockygpt_brain.api.identities.CampusData", return_value=data),
        patch.object(GraphData, "browse", return_value={"total": 1, "diagnostics": []}) as browse,
        patch("rockygpt_brain.api.app.open_gateway") as gateway,
    ):
        response = TestClient(app).get("/v1/dev/graph/collections")
    assert response.status_code == 200
    output = response.json()
    assert {r["id"] for r in output["collections"]} == set(GRAPH_COLLECTIONS)
    menu = next(r for r in output["collections"] if r["id"] == "menu")
    assert [f["key"] for f in menu["group_fields"]][:2] == ["date", "meal"]
    assert output["campus_date"] == "2026-09-21"
    assert browse.call_count == len(GRAPH_COLLECTIONS)
    gateway.assert_not_called()


def test_original_fields_and_provenance_are_not_normalized_or_discarded() -> None:
    graph = GraphData(repository())
    row = {"record": {"id": "one", "source_record_key": "shared", "phone": "x-1234",
                      "hours": None, "aliases": [], "collected_at": NOW.isoformat(),
                      "valid_from": "2026-09-01", "source_id": "source", "name": "Office"},
           "source": {"id": "source", "source_key": "directory", "title": "Directory",
                      "canonical_url": "https://example.edu", "trust_tier": "unreviewed"}}
    original = copy.deepcopy(row)
    record = graph._record("contacts", row)
    assert record["fields"] == {"phone": "x-1234", "hours": None, "aliases": [], "name": "Office"}
    assert record["raw_record"] == original["record"]
    assert record["source_record_id"] == "one" and record["source_record_key"] == "shared"
    assert record["trust_tier"] == "unreviewed"
    assert record["collected_at"] == NOW.isoformat() and "verified_at" not in record
    assert row == original


def test_declared_foreign_keys_produce_browse_navigation() -> None:
    graph = GraphData(repository())
    for collection, target, field in [("documents", "document_chunks", "document_id"),
                                      ("shuttle_routes", "shuttle", "route_id")]:
        record = graph._record(collection, {"record": {"id": "original"}, "source": {}})
        assert record["navigation"][0]["collection"] == target
        assert record["navigation"][0]["filters"] == {field: "original"}


def test_link_scopes_pin_ids_and_ambiguous_legacy_events_fail_closed() -> None:
    data = repository()
    data._artifacts["campus-identities"]["entities"][0]["links"] = [{
        "collection": "events", "source_key": "events", "source_record_keys": ["duplicate"],
        "source_record_ids": ["specific-original-id"],
    }]
    query, params = GraphData(data, UUID(ENTITY_ID))._table_scope("events")
    assert "t.id::text=ANY(%s)" in query.as_string()
    assert params[-1] == ["specific-original-id"]
    del data._artifacts["campus-identities"]["entities"][0]["links"][0]["source_record_ids"]
    query, params = GraphData(data, UUID(ENTITY_ID))._table_scope("events")
    assert "count(*)" in query.as_string()
    assert "duplicate.source_record_key=t.source_record_key)=1" in query.as_string()
    assert params[-1] == ["duplicate"]


def test_broken_links_and_unknown_collections_are_explicit() -> None:
    data = repository()
    data._fetch = Mock(return_value=[])
    graph = GraphData(data, UUID(ENTITY_ID))
    graph._diagnose("contacts")
    assert graph.diagnostics[0]["reason"] == "broken_identity_link"
    assert graph.diagnostics[0]["references"] == ["office:ec"]
    query, _ = graph._table_scope("menu")
    assert "FALSE" in query.as_string()
    assert graph.diagnostics[-1]["reason"] == "no_identity_link"


def test_exact_source_reference_ambiguity_requires_original_id() -> None:
    data = repository()
    data._fetch = Mock(return_value=[{"id": "a"}, {"id": "b"}])
    with pytest.raises(HTTPException) as caught:
        GraphData(data).reference("events", "events", "same-key", None)
    assert caught.value.status_code == 422
    assert "source_record_id" in caught.value.detail


def test_missing_group_value_is_parameterized_null_not_a_fabricated_date() -> None:
    graph = GraphData(repository())
    query, params = graph._filtered_scope("menu", {"date": None, "meal": "Lunch' --"})
    assert "IS NOT DISTINCT FROM %s::jsonb" in query.as_string()
    assert "Lunch" not in query.as_string()
    assert params[-2:] == [None, json.dumps("Lunch' --")]
    assert "America/New_York" in graph._expression("events", "date").as_string()


def test_malformed_projection_is_diagnostic_and_original_artifact_remains_available() -> None:
    data = repository()
    data._load_artifact_records = Mock(side_effect=TypeError("bad artifact"))
    graph = GraphData(data)
    result = graph.browse("faculty", {}, None, 0, 24)
    assert result["records"] == []
    assert result["diagnostics"][0]["reason"] == "artifact_projection_unavailable"
    assert "artifacts" in GRAPH_COLLECTIONS


def test_raw_artifact_null_and_missing_path_are_distinct() -> None:
    data = repository()
    data._fetch = Mock(return_value=[{"present": True, "kind": "null", "total": 0,
                                     "scalar": None, "content_hash": "h", "created_at": NOW}])
    value = GraphData(data).artifact_value("menu", ["unknownLabel"], 0, 24)
    assert value["kind"] == "null" and value["value"] is None
    data._fetch.return_value[0]["present"] = False
    with pytest.raises(HTTPException) as caught:
        GraphData(data).artifact_value("menu", ["missing"], 0, 24)
    assert caught.value.status_code == 404


def test_pagination_truthful_after_last_page() -> None:
    assert _page([], 10, 20, 8)["next_offset"] is None
    assert _page([1, 2], 3, 0, 2)["next_offset"] == 2


@pytest.mark.skipif(not os.getenv("GRAPH_TEST_DATABASE_URL"), reason="read-only dev DB opt-in")
def test_live_release_full_navigation_is_read_only_and_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise actual SQL without creating tables, changing rows, or calling a model."""
    monkeypatch.setenv("DATABASE_URL", os.environ["GRAPH_TEST_DATABASE_URL"])
    client = TestClient(app)
    catalogue = client.get("/v1/dev/graph/collections")
    assert catalogue.status_code == 200, catalogue.text
    version = catalogue.json()["dataset_version"]
    totals = {}
    for collection in catalogue.json()["collections"]:
        name = collection["id"]
        ids = []
        offset = 0
        while True:
            response = client.get("/v1/dev/graph/browse", params={
                "collection": name, "offset": offset, "limit": 100, "dataset_version": version,
            })
            assert response.status_code == 200, response.text
            result = response.json()
            ids.extend(r["id"] for r in result["records"])
            if result["next_offset"] is None:
                break
            assert result["next_offset"] > offset
            offset = result["next_offset"]
        assert len(ids) == len(set(ids)) == collection["total"]
        totals[name] = len(ids)
        if ids:
            detail = client.get("/v1/dev/graph/record", params={
                "collection": name, "record_id": ids[0], "dataset_version": version,
            })
            assert detail.status_code == 200, detail.text
            assert detail.json()["record"]["id"] == ids[0]
    assert totals["document_chunks"] > 100 and totals["menu"] > 100
    dates = client.get("/v1/dev/graph/browse", params={
        "collection": "menu", "group_by": "date", "dataset_version": version,
    }).json()
    values = [group["value"] for group in dates["groups"]]
    assert values == sorted(values)
    assert sum(group["count"] for group in dates["groups"]) == totals["menu"]
    for path in [[], ["dates"], ["dates", "0"]]:
        response = client.get("/v1/dev/graph/value", params={
            "collection": "artifacts", "record_id": "artifacts:menu-week",
            "path": json.dumps(path),
            "dataset_version": version, "limit": 2,
        })
        assert response.status_code == 200, response.text
        assert len(response.json()["children"]) <= 2
        for child in response.json()["children"]:
            leaf = child["kind"] not in {"object", "array"} or child["count"] == 0
            assert ("value" in child) is leaf
            if leaf:
                detail = client.get("/v1/dev/graph/value", params={
                    "collection": "artifacts", "record_id": "artifacts:menu-week",
                    "path": json.dumps(child["path"]), "dataset_version": version,
                })
                assert detail.status_code == 200
                assert child["value"] == detail.json()["value"]


@pytest.mark.parametrize("kind,expected", [("object", {}), ("array", [])])
def test_empty_artifact_containers_keep_their_actual_value(kind: str, expected: Any) -> None:
    data = repository()
    data._fetch = Mock(side_effect=[
        [{"present": True, "kind": kind, "total": 0, "scalar": None,
          "content_hash": "h", "created_at": NOW}], [],
    ])
    value = GraphData(data).artifact_value("menu", ["empty"], 0, 24)
    assert value["value"] == expected and value["children"] == []


def test_artifact_container_labels_preserve_keys_and_never_expose_aggregate_payload() -> None:
    data = repository()
    data._fetch = Mock(side_effect=[
        [{"present": True, "kind": "object", "total": 1, "scalar": None,
          "content_hash": "h", "created_at": NOW}],
        [{"key": "raw_key", "kind": "array", "count": 7, "preview": None,
          "label": "Misleading item title"}],
    ])
    value = GraphData(data).artifact_value("menu", [], 0, 24)
    assert "value" not in value
    assert value["children"][0]["label"] == "raw_key"
    assert value["children"][0]["preview"] == "7 items"
    assert value["children"][0]["path"] == ["raw_key"]


def test_exact_source_reference_passes_original_id_then_preserves_detail() -> None:
    data = repository()
    data._fetch = Mock(return_value=[{"id": "specific-original-id"}])
    graph = GraphData(data)
    with patch.object(graph, "record", return_value={"id": "events:specific-original-id"}) as read:
        result = graph.reference("events", "events", "same-key", "specific-original-id")
    assert result["id"] == "events:specific-original-id"
    read.assert_called_once_with("events", "events:specific-original-id")
    assert data._fetch.call_args.args[1][-1] == "specific-original-id"


@pytest.mark.parametrize("kind,scalar,count,expected", [
    ("string", "complete long leaf " * 100, 0, "complete long leaf " * 100),
    ("string", "", 0, ""),
    ("boolean", False, 0, False),
    ("number", 0, 0, 0),
    ("null", None, 0, None),
    ("array", None, 0, []),
    ("object", None, 0, {}),
])
def test_artifact_children_include_complete_native_leaf_values(
    kind: str, scalar: Any, count: int, expected: Any,
) -> None:
    data = repository()
    data._fetch = Mock(side_effect=[
        [{"present": True, "kind": "object", "total": 1, "scalar": None,
          "content_hash": "h", "created_at": NOW}],
        [{"key": "leaf", "kind": kind, "count": count, "scalar": scalar,
          "preview": str(scalar)[:160], "label": "leaf"}],
    ])
    child = GraphData(data).artifact_value("menu", [], 0, 24)["children"][0]
    assert "value" in child and child["value"] == expected
    assert type(child["value"]) is type(expected)
    assert "scalar" not in child


@pytest.mark.parametrize("kind", ["array", "object"])
def test_artifact_children_omit_nonempty_container_payloads(kind: str) -> None:
    data = repository()
    data._fetch = Mock(side_effect=[
        [{"present": True, "kind": "object", "total": 1, "scalar": None,
          "content_hash": "h", "created_at": NOW}],
        [{"key": "container", "kind": kind, "count": 20, "scalar": None,
          "preview": None, "label": "container"}],
    ])
    child = GraphData(data).artifact_value("menu", [], 0, 24)["children"][0]
    assert "value" not in child and "scalar" not in child
    assert child["count"] == 20
