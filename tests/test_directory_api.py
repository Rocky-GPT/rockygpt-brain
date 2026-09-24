"""Public discovery stays cheap; details share the canonical entity fact reader."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.retrieval.graph import GraphData
from test_profiles import ENTITY_ID, repository, rows


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured")
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "production")


def test_public_directory_returns_one_canonical_entry_without_reading_all_facts() -> None:
    data = repository()
    with (
        patch("rockygpt_brain.api.identities.CampusData", return_value=data),
        patch("rockygpt_brain.retrieval.entity_facts.EntityFacts.build") as facts,
        patch.object(data, "_fetch") as fetch,
        patch.object(data, "close"),
    ):
        response = TestClient(app).get("/v1/directory")
    assert response.status_code == 200
    output = response.json()
    assert output["allContacts"] == [{
        "id": ENTITY_ID, "canonical_entity_id": ENTITY_ID,
        "name": "Example Center", "aliases": ["EC"], "kind": "office", "bucket": "Offices",
        "searchText": "example center ec",
    }]
    assert output["counts"]["total"] == 1
    assert output["dataset_version"] == "test-release"
    assert len(output["identity_hash"]) == 64
    facts.assert_not_called()
    fetch.assert_not_called()


def test_public_entity_facts_use_canonical_contact_properties() -> None:
    data = repository()
    data._artifacts["campus-identity-coverage"] = {"unresolved": []}
    data._artifacts["courses"] = {}
    contact, _ = rows()
    contact["collected_at"] = contact["collected_at"].isoformat()
    record = GraphData(data)._record("contacts", {
        "record": contact, "source": data.sources["directory"],
    })
    identity_hash = data.identity_readiness()["artifact_hash"]
    with (
        patch("rockygpt_brain.api.identities.CampusData", return_value=data),
        patch.object(GraphData, "records", return_value={
            "records": [record], "total": 1, "next_offset": None,
        }),
        patch.object(data, "close"),
    ):
        response = TestClient(app).get(f"/v1/entities/{ENTITY_ID}/facts", params={
            "dataset_version": "test-release", "identity_hash": identity_hash,
        })
    assert response.status_code == 200
    body = response.json()
    assert body["entity"]["id"] == ENTITY_ID
    props = {prop["key"]: prop for prop in body["properties"]}
    assert props["email"]["values"][0]["value"] == "ec@example.edu"
    assert props["phones"]["values"][0]["value"] == [{"number": "201-555-0100"}]
    assert "phone" not in props
    assert body["sources"]
    assert body["record_groups"] == []


@pytest.mark.parametrize("params", [
    {"dataset_version": "old", "identity_hash": "x"},
    {"dataset_version": "test-release", "identity_hash": "old"},
])
def test_public_facts_reject_stale_directory_snapshot(params: dict[str, str]) -> None:
    data = repository()
    with (
        patch("rockygpt_brain.api.identities.CampusData", return_value=data),
        patch("rockygpt_brain.retrieval.entity_facts.EntityFacts.build") as facts,
        patch.object(data, "close"),
    ):
        response = TestClient(app).get(f"/v1/entities/{ENTITY_ID}/facts", params=params)
    assert response.status_code == 409
    facts.assert_not_called()


def test_public_facts_require_both_release_pins() -> None:
    with patch("rockygpt_brain.api.identities.CampusData") as data:
        response = TestClient(app).get(f"/v1/entities/{ENTITY_ID}/facts")
    assert response.status_code == 422
    data.assert_not_called()


def test_directory_names_the_school_a_person_is_part_of() -> None:
    data = repository()
    school_id = "5b0c1f7e-2a4d-4c55-9a3e-1d2f3a4b5c6d"
    data._artifacts["campus-identities"]["entities"] += [
        {
            "id": school_id, "kind": "school", "name": "School of Example Studies", "aliases": [],
            "links": [{"collection": "programs", "source_key": "programs",
                       "source_record_keys": ["school:ses"]}],
        },
        {
            "id": "0d9e8f7a-6b5c-4d3e-8f2a-1b0c9d8e7f6a", "kind": "person",
            "name": "Pat Example", "aliases": [],
            "links": [{"collection": "faculty", "source_key": "faculty",
                       "source_record_keys": ["faculty:pat"]}],
            "relationships": [{
                "type": "part_of", "target_entity_id": school_id,
                "evidence": [{"field": "school", "collection": "faculty", "source_key": "faculty",
                              "source_record_key": "faculty:pat"}],
            }],
        },
    ]
    with (
        patch("rockygpt_brain.api.identities.CampusData", return_value=data),
        patch.object(data, "close"),
    ):
        response = TestClient(app).get("/v1/directory")
    assert response.status_code == 200
    people = {entry["name"]: entry for entry in response.json()["allContacts"]}
    assert people["Pat Example"]["context"] == "School of Example Studies"
    assert "school of example studies" in people["Pat Example"]["searchText"]
    assert "context" not in people["Example Center"]
