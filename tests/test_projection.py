"""Projection v2 keeps assertions, contextual records, sources and identity edges separate."""
from __future__ import annotations

import copy
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, get_args
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.retrieval.data import CampusData
from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.knowledge import course_id
from rockygpt_brain.retrieval.profiles import LinkCollection
from rockygpt_brain.retrieval.projection import (
    MAPPED,
    SCHEDULE_LIMITATION,
    Projection,
    valid_value,
    validate_selection,
)
from rockygpt_brain.retrieval.projection_models import EntityProjection, Property, SourceRecord

NOW = datetime(2026, 9, 21, 16, tzinfo=UTC)
ENTITY_ID = "9f4a8a53-67a1-4ce4-b4da-5d19630f135b"

CLUB = "40b259a0-250e-559d-a166-626aad12b6ee"
EVENT = "00241662-2a28-5c63-a848-4f1de00df57b"
PERSON = "7f9d3c1e-2b4a-4c6d-8e0f-1a2b3c4d5e6f"


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
    for record in records["contacts"]:
        record["raw_record"].update(type="office", status=None, offices=[], phones=[],
                                    preferred_contact=None, prefers_email=False, aliases=[])
        record["fields"].update({k: v for k, v in record["raw_record"].items()
                                 if k in {"type", "status", "offices", "phones", "aliases",
                                          "preferred_contact", "prefers_email"}})
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

    def read(_self: GraphData, collection: str, filters: dict[str, Any], offset: int,
             limit: int) -> dict[str, Any]:
        eligible = [r for r in records.get(collection, []) if all(
            (r["raw_record"].get("valid_from") if k == "date" else r["fields"].get(k)) == v
            for k, v in filters.items())]
        end = offset + limit
        return {"records": eligible[offset:end], "total": len(eligible),
                "next_offset": end if end < len(eligible) else None}

    with patch.object(GraphData, "records", read):
        yield data, snapshot, records


def build(fixture: Fixture, group: str | None = None, filters: dict[str, Any] | None = None,
          limit: int = 2, cursor: str | None = None, entity: str = ENTITY_ID) -> EntityProjection:
    data, snapshot, _ = fixture
    return Projection(data, snapshot).build(UUID(entity), group, filters or {}, limit, cursor)


def values(properties: list[Property]) -> dict[str, list[Any]]:
    return {p.key: [a.value for a in p.assertions] for p in properties}


def source(result: EntityProjection, identifier: str) -> SourceRecord:
    return next(s for s in result.sources if s.id == identifier)


def test_every_linkable_collection_has_an_explicit_mapping() -> None:
    assert set(get_args(LinkCollection)) == MAPPED


def test_three_attachments_preserve_records_conflicts_and_original_data(fixture: Fixture) -> None:
    before = copy.deepcopy(fixture[2])
    result = build(fixture)
    assert values(result.properties)["phone"] == ["one", "two"]
    assert values(result.properties)["name"] == ["Test venue", "Test venue"]
    assert values(result.properties)["prefers_email"] == [False, False]
    assert "search_text" not in values(result.properties)
    assert result.properties_complete
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


def test_each_source_record_is_listed_once_and_every_value_names_it(fixture: Fixture) -> None:
    result = build(fixture)
    listed = [s.id for s in result.sources]
    assert sorted(listed) == sorted({*listed})
    # Two directory entries, two menu offerings on this page and both hours rows.
    assert len(listed) == 6
    phones = next(p for p in result.properties if p.key == "phone").assertions
    assert [a.source_id for a in phones] == ["contacts:1", "contacts:2"]
    assert [a.id for a in phones] == ["contacts:1#phone", "contacts:2#phone"]
    record = result.record_groups[0].records[0]
    assert {a.source_id for p in [*record.context, *record.properties]
            for a in p.assertions} == {record.source_id}
    dumped = result.model_dump(mode="json")
    dumped["properties"][0]["assertions"][0]["source_id"] = "contacts:missing"
    with pytest.raises(ValueError, match="listed source record"):
        EntityProjection.model_validate(dumped)


def test_exact_field_row_dates_and_unknown_dietary_status(fixture: Fixture) -> None:
    result = build(fixture)
    menu = result.record_groups[0].records[0]
    assertion = next(p for p in menu.properties if p.key == "allergens").assertions[0]
    assert assertion.value == [] and assertion.publication_status == "not_published"
    assert any("does not establish absence" in s for s in assertion.limitations)
    assert assertion.field_path == ["allergens"]
    row = source(result, assertion.source_id)
    assert (row.collection, row.row_id, row.source_record_key) == ("menu", "1", "k")
    assert row.valid_from == "2026-09-21"
    assert row.freshness == "stale"
    assert row.collected_at == (NOW - timedelta(days=20)).isoformat()
    # Record-level caveats belong to the source, once, not to each value.
    assert "Not verified current; do not present as current campus facts." in row.limitations
    assert not next(p for p in menu.properties if p.key == "calories").assertions[0].limitations
    fixture[2]["menu"][0]["raw_record"]["collected_at"] = None
    assert source(build(fixture), "menu:1").collected_at is None


def test_hours_keep_weekday_schedule_and_exception_validity_together(fixture: Fixture) -> None:
    result = build(fixture)
    group = next(g for g in result.record_groups if g.key == "dining_hours")
    assert values(group.records[0].context)["weekday"] == ["Monday"]
    assert values(group.records[0].context)["valid_from"] == [None]
    assert values(group.records[1].context)["valid_until"] == ["2026-09-25"]
    assert values(group.records[1].properties)["schedule"] == ["Hours unavailable"]
    assert "name" not in values(group.records[0].properties)
    assert all(SCHEDULE_LIMITATION in source(result, r.source_id).limitations
               for r in group.records)


def test_page_cursor_pins_scope_and_preserves_duplicate_dish_boundaries(fixture: Fixture) -> None:
    first = build(fixture).record_groups[0]
    second = build(fixture, "menu_offerings", cursor=first.next_cursor)
    assert second.selected_record_group == "menu_offerings"
    assert not second.properties_complete and second.properties == []
    assert second.record_groups[0].next_cursor is None
    assert [s.id for s in second.sources] == ["menu:3"]
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
    contact = fixture[2]["contacts"][0]
    contact["raw_record"]["phones"] = [{"number": "201-555-0100", "internal": "SECRET"}]
    result = build(fixture)
    assert "SECRET" not in result.model_dump_json()
    assert {(c.collection, c.fields[0]) for c in result.coverage
            if c.reason == "unsupported_field_shape"} == {("menu", "allergens"),
                                                          ("contacts", "phones")}
    assert any(c.fields == ["future_internal"] for c in result.coverage)
    assert not result.properties_complete


@pytest.mark.parametrize("kind,value,valid", [
    ("phone_list", [{"type": "office", "number": "201-684-9953", "extension": "12"}], True),
    ("phone_list", [{"type": "office"}], False),
    ("phone_list", [{"number": 2016849953}], False),
    ("hours_list", [{"open": "08:00", "close": "00:00", "close_day_offset": 1}], True),
    ("hours_list", [{"open": "08:00", "close": "17:00", "close_day_offset": True}], False),
    ("former_names", [{"name": "School of Theoretical and Applied Science",
                       "evidence": "/tas/ redirects to /snh/"}], True),
    ("former_names", [{"name": "Former"}], False),
    ("credits", 4, True), ("credits", {"min": 0, "max": 4, "operator": ""}, True),
    ("credits", {"min": "0"}, False), ("credits", True, False),
    ("url", "https://www.ramapo.edu/", True), ("text_list", ["a", 1], False),
])
def test_nested_values_follow_their_declared_shape(kind: str, value: Any, valid: bool) -> None:
    assert valid_value(value, kind) is valid


def test_artifact_records_cite_their_artifact_path_and_own_freshness(fixture: Fixture) -> None:
    data, snapshot, records = fixture
    data._artifacts["campus-identities"]["entities"].append({
        "id": PERSON, "kind": "person", "name": "Ada Professor", "aliases": [], "links": [
            {"collection": "faculty", "source_key": "faculty", "source_record_keys": ["ada"]}]})
    profile = {"name": "Ada Professor", "title": "Professor", "school": "SNH",
               "email": "ada@ramapo.edu", "phone": "", "office": "G-201",
               "profileUrl": "https://www.ramapo.edu/snh/faculty/ada/", "imageUrl": "",
               "imagePath": "/images/faculty/ada.jpg", "bio": "", "education": ["Ph.D."],
               "courses": ["CMPS 147"], "teachingInterests": [], "researchInterests": [],
               "publishedResearch": []}
    records["faculty"] = [{
        "id": "faculty:7", "collection": "faculty", "title": "Ada Professor",
        "source_key": "faculty", "source_record_key": "ada", "source_record_id": "7",
        "url": profile["profileUrl"], "collected_at": "2026-09-01T00:00:00+00:00",
        "valid_from": None, "valid_until": None, "freshness": "fresh",
        "limitations": ["Faculty-profile course lists are undated."],
        "fields": profile, "raw_record": profile, "artifact_key": "faculty",
        "artifact_path": ["7"],
    }]
    result = Projection(data, snapshot).build(UUID(PERSON), None, {}, 8, None)
    row = source(result, "faculty:7")
    assert (row.artifact_key, row.artifact_path, row.freshness) == ("faculty", ["7"], "fresh")
    assert row.source_url == profile["profileUrl"]
    assert row.limitations == ["Faculty-profile course lists are undated."]
    assert values(result.properties)["profile_courses"] == [["CMPS 147"]]
    assert values(result.properties)["profile_url"] == [profile["profileUrl"]]
    # A local image path of this repository is not a published campus value.
    assert "/images/faculty/ada.jpg" not in result.model_dump_json()
    assert result.properties_complete and result.coverage == []


def test_a_catalog_course_node_projects_its_own_record(fixture: Fixture) -> None:
    data, snapshot, _ = fixture
    course = {"code": "CMPS 147", "name": "COMPUTER SCIENCE I", "description": "Intro",
              "credits": 4, "attributes": [], "school": "Science, Nursing and Health",
              "conveningGroups": ["Computer Science (CMPS)"],
              "requisites": [{"section": "Prerequisite", "rule": {"condition": "anyOf"}}],
              "requisitesText": "Prerequisite\n  any of\n    MATH 110 PRECALCULUS"}
    parsed = {"id": "courses:CMPS 147", "collection": "courses",
              "title": "CMPS 147 — COMPUTER SCIENCE I", "source_key": "academic-programs",
              "source_record_key": "CMPS 147", "fields": course, "url": "https://catalog",
              "collected_at": None, "valid_from": None, "valid_until": None,
              "freshness": "unknown", "limitations": ["Catalog description only."]}
    data._load_artifact_records = Mock(return_value=[parsed])  # type: ignore[method-assign]
    data._artifacts["courses"] = {"CMPS 147": course}
    node = course_id("academic-programs", "CMPS 147")
    result = Projection(data, snapshot).build(UUID(node), None, {}, 8, None)
    assert result.entity.kind == "course"
    assert values(result.properties)["credits"] == [4]
    assert values(result.properties)["prerequisites"] == [course["requisitesText"]]
    assert values(result.properties)["convening_groups"] == [["Computer Science (CMPS)"]]
    assert values(result.properties)["school"] == ["Science, Nursing and Health"]
    assert source(result, "courses:CMPS 147").artifact_path == ["CMPS 147"]
    # The structured rules behind the listing are neither a fact nor a coverage gap.
    assert "requisites" not in values(result.properties)
    assert not result.coverage
    assert result.properties_complete


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
    # Events and clubs are mapped collections now; nothing waits for migration.
    assert not any(c.reason == "collection_not_migrated" for c in outgoing.coverage)


def test_an_unmapped_collection_is_reported_and_leaves_properties_incomplete(
    fixture: Fixture,
) -> None:
    with patch("rockygpt_brain.retrieval.projection.MAPPED", MAPPED - {"dining_hours"}):
        result = build(fixture)
    assert [c.collection for c in result.coverage
            if c.reason == "collection_not_migrated"] == ["dining_hours"]
    assert not result.properties_complete


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
    ("menu_offerings", {}, "invalid!"), ("operating_hours", {"name": "Library"}, None),
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
    path = "/v1/dev/graph/projection/v2"
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
        response = client.get(path, params=params)
        assert response.status_code == 200
        assert response.json()["schema_version"] == 2
        assert client.get(path, params={**params, "dataset_version": "old"}).status_code == 409
        assert client.get(path, params={**params, "identity_hash": "old"}).status_code == 409
        assert client.get(path, params={**params, "limit": 101}).status_code == 422
        # The v1 projection and the legacy properties reader are retired.
        for retired in ("/v1/dev/graph/projection/v1", "/v1/dev/graph/properties"):
            assert client.get(retired, params=params).status_code == 404
        gateway.assert_not_called()


@pytest.mark.skipif(not os.getenv("GRAPH_TEST_DATABASE_URL"), reason="read-only dev DB opt-in")
def test_live_projection_matches_original_records_for_every_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", os.environ["GRAPH_TEST_DATABASE_URL"])
    client = TestClient(app)
    index = client.get("/v1/dev/graph/knowledge").json()
    pins = {"dataset_version": index["dataset_version"], "identity_hash": index["identity_hash"]}

    def originals(result: dict[str, Any], entity_id: str | None) -> None:
        """Every value equals its original record's field, read through the record API."""
        sources = {s["id"]: s for s in result["sources"]}
        raw: dict[str, Any] = {}
        props = [*result["properties"], *(p for g in result["record_groups"]
                 for r in g["records"] for p in [*r["context"], *r["properties"]])]
        for prop in props:
            for assertion in prop["assertions"]:
                row = sources[assertion["source_id"]]
                if row["id"] not in raw:
                    params = {"collection": row["collection"], "record_id": row["id"],
                              "dataset_version": index["dataset_version"]}
                    if entity_id:
                        params["entity_id"] = entity_id
                    original = client.get("/v1/dev/graph/record", params=params)
                    assert original.status_code == 200, original.text
                    raw[row["id"]] = original.json()["record"]["raw_record"]
                assert assertion["value"] == raw[row["id"]][assertion["field_path"][0]]

    venue = next(e for e in index["nodes"] if e["name"] == "Birch Tree Inn")
    params = {**pins, "entity_id": venue["id"], "limit": 100}
    initial = client.get("/v1/dev/graph/projection/v2", params=params)
    assert initial.status_code == 200, initial.text
    originals(initial.json(), venue["id"])
    hours = next(g for g in initial.json()["record_groups"] if g["key"] == "dining_hours")
    assert len(hours["records"]) == hours["total"] and hours["next_cursor"] is None
    group = next(g for g in initial.json()["record_groups"] if g["key"] == "menu_offerings")
    ids: list[str] = []
    while True:
        ids.extend(record["id"] for record in group["records"])
        if not group["next_cursor"]:
            break
        response = client.get("/v1/dev/graph/projection/v2", params={**params,
            "record_group": "menu_offerings", "cursor": group["next_cursor"]})
        assert response.status_code == 200, response.text
        group = response.json()["record_groups"][0]
    assert len(ids) == len(set(ids)) == group["total"] > 100
    for kind in ["person", "course", "club", "organization", "event", "program", "office",
                 "facility", "venue", "building", "school", "subject"]:
        entity = next(e for e in index["nodes"] if e["kind"] == kind)
        response = client.get("/v1/dev/graph/projection/v2",
                              params={**pins, "entity_id": entity["id"]})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["properties_complete"], (kind, result["coverage"])
        originals(result, None if kind == "course" else entity["id"])
        expected = [e for e in index["edges"] if entity["id"] in {e["source"], e["target"]}]
        assert [(r["subject"]["entity_id"], r["target_entity_id"], r["predicate"], r["evidence"])
                for r in result["relationships"]] == [
                    (e["source"], e["target"], e["type"], e["evidence"]) for e in expected]
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
    # A record whose source cannot be established is not shown at all.
    assert all(not g.records for g in result.record_groups)
    assert all(s.collection == "contacts" for s in result.sources)
    assert sum(c.reason == "source_unavailable" for c in result.coverage) == 4


PLANS = {"captured_at": "2026-09-24T02:00:00Z", "plans": [
    {"id": "plan-2026", "name": "Computer Science", "cohort": "Fall 2026", "variantOf": None,
     "url": "https://www.ramapo.edu/plan-2026/", "applicability": "Students admitted in 2026.",
     "totalCredits": 128, "graduateCredits": None, "gpa": "2.0",
     "totals": ["Total Credits Required: 128 credits", "GPA: 2.0"],
     "planText": "First Year, Fall Semester (16 credits)",
     "placementText": "Math Placement: MATH 110-121", "generalEducationText": "Global Awareness",
     "notes": ["WI: Writing Intensive-3 required in the major"],
     "documents": [{"name": "PDF", "url": "https://www.ramapo.edu/plan.pdf"}],
     "terms": [{"year": "First Year", "term": "Fall Semester", "items": []}], "limitations": []},
    {"id": "plan-4-1", "name": "Computer Science with MS in Data Science 4+1",
     "cohort": "Fall 2023", "variantOf": "Computer Science",
     "url": "https://www.ramapo.edu/plan-2023/", "finalUrl": "https://www.ramapo.edu/moved/",
     "applicability": None, "planText": None, "placementText": None,
     "generalEducationText": None, "graduateCredits": 30, "totals": [],
     "totalCredits": None, "gpa": None, "notes": [], "documents": [], "limitations": [
         "This section lists the plan without a major; the index lists the same plan under "
         "Computer Science in another cohort."]},
    {"id": "unlinked", "name": "Undecided", "cohort": "Fall 2026", "limitations": []},
]}


def test_a_program_lists_its_graduation_plans_by_cohort_with_their_caveats(
    fixture: Fixture,
) -> None:
    data, snapshot, records = fixture
    data.sources["graduation-plans"] = {**data.sources["directory"], "id": "graduation-plans",
                                        "source_key": "graduation-plans"}
    data._artifacts["graduation-plans"] = PLANS
    entity = data._artifacts["campus-identities"]["entities"][0]
    entity.update(kind="program", links=[{
        "collection": "graduation_plans", "source_key": "graduation-plans",
        "source_record_keys": ["plan-2026", "plan-4-1"]}])
    loaded = data._load_artifact_records("graduation_plans")
    assert [record["title"] for record in loaded] == [
        "Computer Science — Fall 2026", "Computer Science with MS in Data Science 4+1 — Fall 2023",
        "Undecided — Fall 2026"]
    # The plans' own capture time, the structured semesters left in the original item.
    assert datetime.fromisoformat(loaded[0]["collected_at"]) == datetime(2026, 9, 24, 2, tzinfo=UTC)
    assert "terms" not in loaded[0]["fields"]
    assert loaded[1]["url"] == "https://www.ramapo.edu/moved/"
    assert "another cohort" in loaded[1]["limitations"][-1]
    assert "does not replace advising" in loaded[0]["limitations"][-1]
    reader = GraphData(data)
    records["graduation_plans"] = [
        reader._artifact_record("graduation_plans", record) for record in loaded
        if record["source_record_key"] != "unlinked"]
    result = build(fixture, "graduation_plans", limit=8)
    [group] = result.record_groups
    assert (group.label, group.total) == ("Graduation plans", 2)
    first, variant = group.records
    assert values(first.context) == {"cohort": ["Fall 2026"], "variant_of": [None]}
    assert values(variant.context)["variant_of"] == ["Computer Science"]
    props = values(first.properties)
    assert props["total_credits"] == [128] and props["gpa"] == ["2.0"]
    assert props["totals"] == [["Total Credits Required: 128 credits", "GPA: 2.0"]]
    assert values(variant.properties)["graduate_credits"] == [30]
    # The index's order: the Fall 2026 plan before the Fall 2023 variant.
    assert [record.label for record in group.records][0] == "Computer Science — Fall 2026"
    assert props["documents"] == [[{"name": "PDF", "url": "https://www.ramapo.edu/plan.pdf"}]]
    assert props["notes"] == [["WI: Writing Intensive-3 required in the major"]]
    # The structured semesters and page text remain in the original item, not as gaps.
    assert variant.id and source(result, variant.source_id).artifact_path == ["plans", "1"]
    assert not result.coverage
