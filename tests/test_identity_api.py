"""Development identity inspection reuses read-only source-grounded retrieval."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from test_profile_sections import PERSON, PROGRAM, full_dining_data, program_data
from test_profiles import ENTITY_ID, attach_rows, repository, rows


@pytest.fixture(autouse=True)
def development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured")


def inspection_data() -> Any:
    data = repository()
    data._artifacts["campus-identity-coverage"] = {
        "identity_count": 1,
        "identities_by_kind": {"office": 1},
        "linked_records": {"contacts": 1, "campus_hours": 1},
        "relationships": {},
        "unresolved": [{"collection": "contacts", "record": "unlinked-record",
                        "reason": "No approved identity link"}],
    }
    return data


@pytest.mark.parametrize("environment", [None, "production", "staging", "Development"])
@pytest.mark.parametrize("path", [
    "/v1/dev/identities", "/v1/dev/identities/not-a-uuid?menu_limit=1000",
])
def test_identity_inspection_is_hidden_outside_development(
    monkeypatch: pytest.MonkeyPatch, environment: str | None, path: str,
) -> None:
    if environment is None:
        monkeypatch.delenv("BRAIN_ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("BRAIN_ENVIRONMENT", environment)
    with patch("rockygpt_brain.api.identities.CampusData") as data:
        response = TestClient(app).get(path)
    assert response.status_code == 404
    data.assert_not_called()


def test_identity_index_keeps_original_links_relationships_and_release_scope() -> None:
    data = inspection_data()
    data._artifacts["campus-identity-coverage"]["unexpected_field"] = "do-not-expose"
    with (
        patch("rockygpt_brain.api.identities.CampusData", return_value=data),
        patch("rockygpt_brain.api.app.open_gateway") as gateway,
        patch("rockygpt_brain.api.app.run_turn") as run,
        patch.object(data, "close") as close,
    ):
        response = TestClient(app).get("/v1/dev/identities")
    assert response.status_code == 200
    output = response.json()
    assert output["dataset_version"] == "test-release"
    assert output["campus_date"] == "2026-09-21"
    assert len(output["identity_hash"]) == 64
    assert output["identities"][0]["id"] == ENTITY_ID
    assert output["identities"][0]["links"][0]["source_record_keys"] == ["office:ec"]
    assert output["identities"][0]["relationships"] == []
    assert output["coverage"]["unresolved"][0]["record"] == "unlinked-record"
    assert "do-not-expose" not in response.text
    gateway.assert_not_called()
    run.assert_not_called()
    close.assert_called_once()


@pytest.mark.parametrize("payload", [None, {}, {"identity_count": 99}])
def test_missing_or_invalid_coverage_does_not_claim_complete_coverage(payload: Any) -> None:
    data = inspection_data()
    data._artifacts["campus-identity-coverage"] = payload
    with patch("rockygpt_brain.api.identities.CampusData", return_value=data):
        response = TestClient(app).get("/v1/dev/identities")
    assert response.status_code == 200
    assert response.json()["coverage"] is None
    assert len(response.json()["identities"]) == 1


def test_coverage_for_a_different_identity_count_is_not_displayed() -> None:
    data = inspection_data()
    data._artifacts["campus-identity-coverage"]["identity_count"] = 2
    with patch("rockygpt_brain.api.identities.CampusData", return_value=data):
        response = TestClient(app).get("/v1/dev/identities")
    assert response.status_code == 200
    assert response.json()["coverage"] is None


def test_graph_relationship_targets_retrieve_original_convener_evidence() -> None:
    data = program_data()
    data._artifacts["campus-identity-coverage"] = None
    with patch("rockygpt_brain.api.identities.CampusData", return_value=data):
        index = TestClient(app).get("/v1/dev/identities").json()
        response = TestClient(app).get(
            f"/v1/dev/identities/{PROGRAM}", params={"include": "conveners"},
        )
    program = next(entity for entity in index["identities"] if entity["id"] == PROGRAM)
    relationship = program["relationships"][0]
    assert relationship["target_entity_id"] == PERSON
    assert relationship["evidence"][0]["field"] == "customFields.rJQmj"
    assert relationship["evidence"][0]["source_record_key"] == "School:Computer Science"
    assert response.status_code == 200
    profile = response.json()["profile"]
    actual = profile["components"]["conveners"]["relationships"][0]
    assert actual["target"]["id"] == PERSON
    assert actual["evidence_ids"] == ["programs:program:convener"]
    assert profile["records"][1]["collected_at"] == "2026-09-20T12:00:00+00:00"


@pytest.mark.parametrize("payload", [None, {}, {"schema_version": 2, "entities": []}])
@pytest.mark.parametrize("path", ["/v1/dev/identities", f"/v1/dev/identities/{ENTITY_ID}"])
def test_missing_or_invalid_registry_is_unavailable(payload: Any, path: str) -> None:
    data = inspection_data()
    data._artifacts["campus-identities"] = payload
    with (
        patch("rockygpt_brain.api.identities.CampusData", return_value=data),
        patch.object(data, "close") as close,
    ):
        response = TestClient(app).get(path)
    assert response.status_code == 503
    close.assert_called_once()


def test_missing_database_is_unavailable_without_creating_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL")
    with patch("rockygpt_brain.api.identities.CampusData") as data:
        response = TestClient(app).get("/v1/dev/identities")
    assert response.status_code == 503
    data.assert_not_called()


def test_database_errors_close_connection_and_do_not_expose_connection_details() -> None:
    with patch("rockygpt_brain.api.identities.CampusData") as data:
        data.return_value.identity_readiness.side_effect = RuntimeError("secret database detail")
        response = TestClient(app).get("/v1/dev/identities")
    assert response.status_code == 503
    assert "secret" not in response.text
    data.return_value.close.assert_called_once()


def test_profile_uses_requested_sections_original_evidence_and_campus_service_date() -> None:
    data = inspection_data()
    fetch = attach_rows(data, *rows())
    with (
        patch("rockygpt_brain.api.identities.CampusData", return_value=data),
        patch("rockygpt_brain.api.app.open_gateway") as gateway,
    ):
        response = TestClient(app).get(
            f"/v1/dev/identities/{ENTITY_ID}",
            params={"include": "contact,hours", "date": "2026-09-21",
                    "dataset_version": "test-release", "menu_limit": 12},
        )
    assert response.status_code == 200
    output = response.json()
    assert output["dataset_version"] == output["profile"]["dataset_version"] == "test-release"
    assert set(output["profile"]["components"]) == {"contact", "hours"}
    assert output["profile"]["records"][0]["id"] == "contacts:contact"
    assert output["profile"]["records"][1]["fields"]["service_date"] == "2026-09-21"
    assert output["profile"]["records"][1]["fields"]["hours"][0]["close_day_offset"] == 1
    assert fetch.call_count == 2
    gateway.assert_not_called()


def test_default_profile_sections_remain_selective_and_date_defaults_to_campus_today() -> None:
    data = inspection_data()
    attach_rows(data, *rows())
    with patch("rockygpt_brain.api.identities.CampusData", return_value=data):
        response = TestClient(app).get(f"/v1/dev/identities/{ENTITY_ID}")
    assert response.status_code == 200
    output = response.json()["profile"]
    assert set(output["components"]) == {
        "contact", "hours", "faculty", "courses", "program", "conveners", "menu",
    }
    assert output["components"]["hours"]["service_date"] == data.today.isoformat()
    assert output["components"]["faculty"]["status"] == "missing"


@pytest.mark.parametrize("limit,expected,omitted", [(12, 12, 40), (100, 52, 0)])
def test_dining_selection_keeps_meal_date_hours_and_truthful_menu_coverage(
    limit: int, expected: int, omitted: int,
) -> None:
    data = full_dining_data()
    with patch("rockygpt_brain.api.identities.CampusData", return_value=data):
        response = TestClient(app).get(
            f"/v1/dev/identities/{ENTITY_ID}",
            params={"include": "hours,menu", "date": "2026-09-21", "meal": "Lunch",
                    "menu_limit": limit},
        )
    assert response.status_code == 200
    profile = response.json()["profile"]
    menu = profile["components"]["menu"]
    assert menu["service_date"] == "2026-09-21" and menu["meal"] == "Lunch"
    assert menu["returned_count"] == expected and menu["omitted_count"] == omitted
    assert menu["total_matches"] == 52
    assert len(profile["records"]) == expected + 1
    assert profile["components"]["hours"]["status"] == "available"
    assert profile["records"][0]["source_record_key"] == "exception"


@pytest.mark.parametrize("params", [
    {"include": "contact,contact"}, {"include": "arbitrary_table"}, {"include": ""},
    {"date": "2026-02-30"}, {"meal": " "}, {"menu_limit": 0}, {"menu_limit": 101},
])
def test_invalid_profile_selections_never_fetch_data(params: dict[str, Any]) -> None:
    with patch("rockygpt_brain.api.identities.CampusData") as data:
        response = TestClient(app).get(f"/v1/dev/identities/{ENTITY_ID}", params=params)
    assert response.status_code == 422
    data.assert_not_called()


def test_changed_release_is_a_conflict_before_original_record_retrieval() -> None:
    data = inspection_data()
    data._fetch = Mock(side_effect=AssertionError("Must not retrieve another release"))
    with patch("rockygpt_brain.api.identities.CampusData", return_value=data):
        response = TestClient(app).get(
            f"/v1/dev/identities/{ENTITY_ID}", params={"dataset_version": "earlier-release"},
        )
    assert response.status_code == 409
    data._fetch.assert_not_called()


def test_unknown_identity_is_not_an_empty_profile() -> None:
    data = inspection_data()
    with patch("rockygpt_brain.api.identities.CampusData", return_value=data):
        response = TestClient(app).get(
            "/v1/dev/identities/071ef096-baa7-441c-a1d6-62388006cc59"
        )
    assert response.status_code == 404


def test_inspection_clock_uses_current_campus_date_not_utc_date() -> None:
    data = inspection_data()
    data.today = date(2026, 9, 21)
    instant = datetime(2026, 9, 22, 2, tzinfo=UTC)
    with (
        patch("rockygpt_brain.api.identities.datetime") as clock,
        patch("rockygpt_brain.api.identities.CampusData", return_value=data) as factory,
    ):
        clock.now.return_value = instant
        response = TestClient(app).get("/v1/dev/identities")
    assert response.status_code == 200
    assert response.json()["campus_date"] == "2026-09-21"
    assert factory.call_args.args[1] == instant
