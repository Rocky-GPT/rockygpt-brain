"""Projection v1 keeps assertions, contextual tuples and identity edges separate."""
from __future__ import annotations

import copy
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.retrieval.data import CampusData
from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.projection import Projection, validate_selection
from rockygpt_brain.retrieval.projection_models import EntityProjection, Property

NOW = datetime(2026, 9, 21, 16, tzinfo=UTC)
ENTITY_ID = "9f4a8a53-67a1-4ce4-b4da-5d19630f135b"

CLUB = "40b259a0-250e-559d-a166-626aad12b6ee"
EVENT = "00241662-2a28-5c63-a848-4f1de00df57b"


Fixture = tuple[CampusData, dict[str, Any], dict[str, list[dict[str, Any]]]]


@pytest.fixture
def fixture() -> Iterator[Fixture]:
    data = CampusData("postgresql://unused", NOW)
    data.dataset = {"id": "dataset-one", "version": "test-release"}
    data.sources = {key: {"id": key, "source_key": key, "title": key,
        "canonical_url": f"https://example.edu/{key}", "trust_tier": "official_primary",
        "freshness_sla_hours": 168, "provenance_status": "success"}
        for key in ["directory", "dining"]}
    data._artifacts["campus-identities"] = {"schema_version": 1, "entities": [{
        "id": ENTITY_ID, "aliases": [],
    }]}
    data._artifacts["campus-identity-coverage"] = {"unresolved": []}
    data._artifacts["courses"] = {}
    data._artifacts["catalog-course-identities"] = None
    entity = data._artifacts["campus-identities"]["entities"][0]
    entity.update(kind="venue", name="Test venue", links=[
        {"collection": c, "source_key": s, "source_record_keys": ["k"]}
        for c, s in [("contacts", "directory"), ("dining_hours", "dining"), ("menu", "dining")]
    ])
    snapshot = {"dataset_version": "test-release", "identity_hash": "identity-hash"}
    records: dict[str, list[dict[str, Any]]] = {"contacts": [], "dining_hours": [], "menu": []}

    def row(collection: str, number: int, **fields: Any) -> dict[str, Any]:
        source = data.sources["directory" if collection == "contacts" else "dining"]
        raw = {"id": str(number), "source_id": source["id"], "source_record_key": "k",
               "collected_at": (NOW - timedelta(days=20)).isoformat(),
               "valid_from": None, "valid_until": None, **fields}
        result = GraphData(data)._record(collection, {"record": raw, "source": source})
        records[collection].append(result)
        return result

    row("contacts", 1, name="Test venue", email="venue@example.edu", phone="one",
        title=None, office=None, department="Dining", contact_note="", search_text="INTERNAL")
    row("contacts", 2, name="Test venue", email="other@example.edu", phone="two",
        title=None, office=None, department="Dining", contact_note="")
    for number, day, meal, calories in [(1, "2026-09-21", "Lunch", 0),
                                       (2, "2026-09-22", "Dinner", 420),
                                       (3, "2026-09-23", "Lunch", None)]:
        row("menu", number, name="Same dish", meal=meal, station="Grill", calories=calories,
            portion_size="", vegan=False, vegetarian=None, allergens=[],
            label_coverage={"vegan": "published", "vegetarian": "not_published",
                            "allergens": "not_published"}, valid_from=day, valid_until=day)
    row("dining_hours", 1, name="Test venue", day="Monday", schedule="Lunch: 11:00–14:00")
    row("dining_hours", 2, name="Test venue", day="Monday", schedule="Hours unavailable",
        valid_from="2026-09-21", valid_until="2026-09-25")

    def read(_self: Projection, _reader: GraphData, collection: str,
             filters: dict[str, Any], offset: int, limit: int
             ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        eligible = [r for r in records[collection] if all(
            (r["raw_record"].get("valid_from") if k == "date" else r["fields"].get(k)) == v
            for k, v in filters.items())]
        end = offset + limit
        page = {"total": len(eligible), "next_offset": end if end < len(eligible) else None}
        return page, eligible[offset:end]

    with patch.object(Projection, "_records", read):
        yield data, snapshot, records


def build(fixture: Fixture, group: str | None = None, filters: dict[str, Any] | None = None,
          limit: int = 2, cursor: str | None = None, entity: str = ENTITY_ID) -> EntityProjection:
    data, snapshot, _ = fixture
    return Projection(data, snapshot).build(UUID(entity), group, filters or {}, limit, cursor)


def values(properties: list[Property]) -> dict[str, list[Any]]:
    return {p.key: [a.value for a in p.assertions] for p in properties}


def test_three_attachments_preserve_records_conflicts_and_original_data(fixture: Fixture) -> None:
    before = copy.deepcopy(fixture[2])
    result = build(fixture)
    assert values(result.properties)["phone"] == ["one", "two"]
    assert values(result.properties)["name"] == ["Test venue", "Test venue"]
    assert "search_text" not in values(result.properties)
    menu = next(g for g in result.record_groups if g.key == "menu_offerings")
    assert menu.total == 3 and menu.returned == 2 and menu.next_cursor
    assert len({r.id for r in menu.records}) == 2
    assert [values(r.context)["valid_from"] for r in menu.records] == [["2026-09-21"],
                                                                      ["2026-09-22"]]
    assert [values(r.properties)["calories"] for r in menu.records] == [[0], [420]]
    assert values(menu.records[0].properties)["vegan"] == [False]
    assert values(menu.records[0].properties)["vegetarian"] == [None]
    assert values(menu.records[0].properties)["portion_size"] == [""]
    assert result.relationships == []
    assert fixture[2] == before
    EntityProjection.model_validate_json(result.model_dump_json())


def test_exact_field_row_dates_and_unknown_dietary_status(fixture: Fixture) -> None:
    result = build(fixture)
    menu = result.record_groups[0].records[0]
    prop = next(p for p in menu.properties if p.key == "allergens")
    assertion = prop.assertions[0]
    assert assertion.value == [] and assertion.publication_status == "not_published"
    assert any("does not establish absence" in s for s in assertion.limitations)
    provenance = assertion.provenance[0]
    assert provenance.locator.model_dump() == {
        "kind": "row", "collection": "menu", "row_id": "1", "field_path": ["allergens"]}
    assert provenance.source_record_key == "k"
    assert provenance.valid_from == "2026-09-21"
    assert provenance.freshness == "stale"
    assert provenance.collected_at == (NOW - timedelta(days=20)).isoformat()
    fixture[2]["menu"][0]["raw_record"]["collected_at"] = None
    assert build(fixture).record_groups[0].records[0].properties[0].assertions[0].provenance[
        0].collected_at is None


def test_hours_keep_weekday_schedule_and_exception_validity_together(fixture: Fixture) -> None:
    group = next(g for g in build(fixture).record_groups if g.key == "dining_hours")
    assert values(group.records[0].context)["weekday"] == ["Monday"]
    assert values(group.records[0].context)["valid_from"] == [None]
    assert values(group.records[1].context)["valid_until"] == ["2026-09-25"]
    assert values(group.records[1].properties)["schedule"] == ["Hours unavailable"]
    assert "name" not in values(group.records[0].properties)
    assert all(r.properties[0].assertions[0].limitations for r in group.records)


def test_page_cursor_pins_scope_and_preserves_duplicate_dish_boundaries(fixture: Fixture) -> None:
    first = build(fixture).record_groups[0]
    second = build(fixture, "menu_offerings", cursor=first.next_cursor)
    assert second.selected_record_group == "menu_offerings"
    assert not second.properties_complete and second.properties == []
    assert second.record_groups[0].next_cursor is None
    assert len({r.id for r in [*first.records, *second.record_groups[0].records]}) == 3
    changes: list[dict[str, Any]] = [
        {"group": "dining_hours"}, {"limit": 1}, {"filters": {"meal": "Dinner"}}]
    for kwargs in changes:
        with pytest.raises(HTTPException) as caught:
            build(fixture, cursor=first.next_cursor, **{"group": "menu_offerings", **kwargs})
        assert caught.value.status_code == 409
    fixture[1]["dataset_version"] = "new-release"
    with pytest.raises(HTTPException, match="scope changed"):
        build(fixture, "menu_offerings", cursor=first.next_cursor)


def test_filters_keep_correct_context_and_empty_groups_are_truthful(fixture: Fixture) -> None:
    selected = build(fixture, "menu_offerings", {"meal": "Dinner"}).record_groups[0]
    assert selected.total == 1
    assert values(selected.records[0].properties)["calories"] == [420]
    empty = build(fixture, "menu_offerings", {"date": "1900-01-01"}).record_groups[0]
    assert empty.total == 0 and empty.records == [] and empty.next_cursor is None


def test_unknown_and_nested_storage_fields_are_not_blindly_published(fixture: Fixture) -> None:
    row = fixture[2]["menu"][0]
    row["raw_record"]["allergens"] = [{"name": "Egg", "internal": "SECRET"}]
    row["fields"]["future_internal"] = "SECRET"
    result = build(fixture)
    assert "SECRET" not in result.model_dump_json()
    assert any(c.reason == "unsupported_field_shape" for c in result.coverage)
    assert any(c.fields == ["future_internal"] for c in result.coverage)


def test_relationship_direction_pinned_evidence_and_registry_location(fixture: Fixture) -> None:
    data, _, _ = fixture
    evidence = [{"collection": "events", "source_key": "archway-events",
                 "source_record_key": "occurrence", "source_record_id": EVENT,
                 "field": "organizer_group_id", "source_url": "https://example.edu/event"}]
    data._artifacts["campus-identities"]["entities"].extend([
        {"id": CLUB, "kind": "club", "name": "Test club", "aliases": [], "links": [
            {"collection": "clubs", "source_key": "clubs", "source_record_keys": ["club"]}]},
        {"id": EVENT, "kind": "event", "name": "Test event", "aliases": [], "links": [
            {"collection": "events", "source_key": "events", "source_record_keys": ["event"]}],
         "relationships": [{"type": "organized_by", "target_entity_id": CLUB,
                            "evidence": evidence}]},
    ])
    outgoing, incoming = build(fixture, entity=EVENT), build(fixture, entity=CLUB)
    assert outgoing.relationships[0].id == incoming.relationships[0].id
    assert outgoing.relationships[0].direction == "outgoing"
    assert incoming.relationships[0].direction == "incoming"
    subject = incoming.relationships[0].subject
    assert subject.kind == "entity" and subject.entity_id == EVENT
    assert outgoing.relationships[0].evidence == evidence
    assert outgoing.relationships[0].registry_locator.relationship_index == 0
    assert outgoing.coverage[0].reason == "collection_not_migrated"
    assert not outgoing.properties_complete


def test_repeated_declarations_remain_separate_occurrences(fixture: Fixture) -> None:
    data, _, _ = fixture
    evidence = [{"collection": "events", "source_key": "archway-events",
                 "source_record_key": "occurrence", "source_record_id": EVENT,
                 "field": "organizer_group_id"}]
    repeated = {"type": "organized_by", "target_entity_id": CLUB, "evidence": evidence}
    data._artifacts["campus-identities"]["entities"].extend([
        {"id": CLUB, "kind": "club", "name": "Test club", "aliases": [], "links": [
            {"collection": "clubs", "source_key": "clubs", "source_record_keys": ["club"]}]},
        {"id": EVENT, "kind": "event", "name": "Test event", "aliases": [], "links": [
            {"collection": "events", "source_key": "events", "source_record_keys": ["event"]}],
         "relationships": [repeated, dict(repeated)]},
    ])
    for projection in (build(fixture, entity=EVENT), build(fixture, entity=CLUB)):
        located = [(r.id, r.registry_locator.relationship_index) for r in projection.relationships]
        assert [index for _, index in located] == [0, 1]
        assert len({identifier for identifier, _ in located}) == 2
    outgoing, incoming = build(fixture, entity=EVENT), build(fixture, entity=CLUB)
    assert [r.id for r in outgoing.relationships] == [r.id for r in incoming.relationships]


@pytest.mark.parametrize("group,filters,cursor", [
    (None, {"meal": "Lunch"}, None), ("unknown", {}, None),
    ("menu_offerings", {"name": "dish"}, None), ("menu_offerings", {"meal": []}, None),
    ("menu_offerings", {}, "invalid!"),
])
def test_invalid_selection_fails_before_read(
    group: str | None, filters: dict[str, Any], cursor: str | None,
) -> None:
    with pytest.raises(HTTPException) as caught:
        validate_selection(group, filters, cursor)
    assert caught.value.status_code == 422


def test_endpoint_is_development_only_pinned_and_does_not_call_models(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, _, _ = fixture
    client = TestClient(app)
    path = "/v1/dev/graph/projection/v1"
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "production")
    with patch("rockygpt_brain.api.identities.CampusData") as factory:
        assert client.get(path).status_code == 404
        factory.assert_not_called()
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    data.identity_readiness = Mock(  # type: ignore[method-assign]
        return_value={"artifact_hash": "identity-hash"})
    params = {"entity_id": ENTITY_ID, "dataset_version": "test-release",
              "identity_hash": "identity-hash"}
    with (patch("rockygpt_brain.api.identities.CampusData", return_value=data),
          patch("rockygpt_brain.api.app.open_gateway") as gateway):
        assert client.get(path, params=params).status_code == 200
        assert client.get(path, params={**params, "dataset_version": "old"}).status_code == 409
        assert client.get(path, params={**params, "identity_hash": "old"}).status_code == 409
        assert client.get(path, params={**params, "limit": 101}).status_code == 422
        gateway.assert_not_called()


@pytest.mark.skipif(not os.getenv("GRAPH_TEST_DATABASE_URL"), reason="read-only dev DB opt-in")
def test_live_dining_pagination_provenance_and_unrelated_legacy_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", os.environ["GRAPH_TEST_DATABASE_URL"])
    client = TestClient(app)
    index = client.get("/v1/dev/graph/knowledge").json()
    pins = {"dataset_version": index["dataset_version"], "identity_hash": index["identity_hash"]}
    venue = next(e for e in index["nodes"] if e["name"] == "Birch Tree Inn")
    params = {**pins, "entity_id": venue["id"], "limit": 100}
    initial = client.get("/v1/dev/graph/projection/v1", params=params)
    assert initial.status_code == 200, initial.text
    for record_group in initial.json()["record_groups"]:
        sample = record_group["records"][0]
        assertion = sample["properties"][0]["assertions"][0]
        locator = assertion["provenance"][0]["locator"]
        original = client.get("/v1/dev/graph/record", params={
            "entity_id": venue["id"], "dataset_version": index["dataset_version"],
            "collection": locator["collection"],
            "record_id": f"{locator['collection']}:{locator['row_id']}",
        })
        assert original.status_code == 200, original.text
        raw = original.json()["record"]["raw_record"]
        for prop in sample["context"] + sample["properties"]:
            value = prop["assertions"][0]
            assert value["value"] == raw[value["provenance"][0]["locator"]["field_path"][0]]
    hours = next(g for g in initial.json()["record_groups"] if g["key"] == "dining_hours")
    assert len(hours["records"]) == hours["total"] and hours["next_cursor"] is None
    group = next(g for g in initial.json()["record_groups"] if g["key"] == "menu_offerings")
    ids = []
    while True:
        for record in group["records"]:
            ids.append(record["id"])
            assertions = [p["assertions"][0] for p in record["properties"] + record["context"]]
            assert len({a["provenance"][0]["locator"]["row_id"] for a in assertions}) == 1
            assert all(a["provenance"][0]["source_record_key"] for a in assertions)
        if not group["next_cursor"]:
            break
        response = client.get("/v1/dev/graph/projection/v1", params={**params,
            "record_group": "menu_offerings", "cursor": group["next_cursor"]})
        assert response.status_code == 200, response.text
        group = response.json()["record_groups"][0]
    assert len(ids) == len(set(ids)) == group["total"] > 100
    for kind in ["person", "course", "club", "event", "program", "office", "facility"]:
        entity = next(e for e in index["nodes"] if e["kind"] == kind)
        old_params = {"entity_id": entity["id"], "dataset_version": index["dataset_version"]}
        before = client.get("/v1/dev/graph/properties", params=old_params).json()
        response = client.get("/v1/dev/graph/projection/v1",
                              params={**pins, "entity_id": entity["id"]})
        assert response.status_code == 200, response.text
        result = response.json()
        expected = [e for e in index["edges"] if entity["id"] in {e["source"], e["target"]}]
        assert [(r["subject"]["entity_id"], r["target_entity_id"], r["predicate"], r["evidence"])
                for r in result["relationships"]] == [
                    (e["source"], e["target"], e["type"], e["evidence"]) for e in expected]
        after = client.get("/v1/dev/graph/properties", params=old_params).json()
        assert before == after
    assert client.get("/v1/dev/graph/knowledge").json() == index


def test_cursor_rejects_identity_projection_and_entity_changes(fixture: Fixture) -> None:
    first = build(fixture).record_groups[0]
    for key in ["identity_hash", "dataset_version"]:
        previous = fixture[1][key]
        fixture[1][key] = "changed"
        with pytest.raises(HTTPException) as caught:
            build(fixture, "menu_offerings", cursor=first.next_cursor)
        assert caught.value.status_code == 409
        fixture[1][key] = previous
    with patch("rockygpt_brain.retrieval.projection.PROJECTION_VERSION", "next-spec"):
        with pytest.raises(HTTPException) as caught:
            build(fixture, "menu_offerings", cursor=first.next_cursor)
        assert caught.value.status_code == 409
    fixture[0]._artifacts["campus-identities"]["entities"].append({
        "id": CLUB, "kind": "venue", "name": "Other venue", "aliases": [], "links": [
            {"collection": "menu", "source_key": "dining", "source_record_keys": ["other"]}],
    })
    with pytest.raises(HTTPException) as caught:
        build(fixture, "menu_offerings", cursor=first.next_cursor, entity=CLUB)
    assert caught.value.status_code == 409


def test_partial_property_coverage_is_explicit(fixture: Fixture) -> None:
    with patch("rockygpt_brain.retrieval.projection.PROPERTY_RECORD_LIMIT", 1):
        result = build(fixture)
    assert not result.properties_complete
    assert any(c.reason == "property_limit" for c in result.coverage)
    assert len(values(result.properties)["phone"]) == 1
    del fixture[2]["contacts"][0]["raw_record"]["email"]
    result = build(fixture)
    assert not result.properties_complete
    assert any(c.reason == "field_unavailable" and c.fields == ["email"] for c in result.coverage)


def test_unmapped_nested_fields_and_missing_sources_fail_closed(fixture: Fixture) -> None:
    raw = fixture[2]["menu"][0]["raw_record"]
    raw["label_coverage"]["vegan"] = {"internal": "HIDDEN"}
    result = build(fixture)
    assert "HIDDEN" not in result.model_dump_json()
    assert any(c.reason == "unsupported_field_shape" for c in result.coverage)
    del fixture[0].sources["dining"]
    result = build(fixture)
    assert all(not r.properties and not r.context
               for g in result.record_groups for r in g.records)
    assert any(c.reason == "source_unavailable" for c in result.coverage)
