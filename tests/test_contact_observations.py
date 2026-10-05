"""Field capture provenance changes freshness without refreshing unrelated facts."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from rockygpt_brain.answers import render_facts
from rockygpt_brain.retrieval import EvidenceUnavailable, MemoryEntityFacts
from rockygpt_brain.retrieval.entity_facts import _projection_hash, canonical_properties

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
ARTIFACT_HASH = "a" * 64
OLD_CAPTURE = "2026-09-01T10:00:00+00:00"


def observed_row(*fields: str, **changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "r1", "source_key": "directory", "source_record_key": "office:registrar",
        "name": "Registrar", "email": "registrar@example.edu", "phone": "+12015550100",
        "phones": [{"number": "+12015550100", "type": "voice", "extension": None}],
        "office": "D224", "offices": ["D224"], "collected_at": OLD_CAPTURE,
        "canonical_url": "https://example.edu/directory", "freshness_sla_hours": 24,
        "contact_observation_artifact_hash": ARTIFACT_HASH, **changes,
    }
    projections = {
        "email": row["email"], "phones": {"phone": row["phone"], "phones": row["phones"]},
        "offices": {"office": row["office"], "offices": row["offices"]},
    }
    observations = {}
    for field in fields:
        raw = json.dumps(projections[field], sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode()
        observations[field] = {
            "captured_at": "2026-10-01T10:00:00+00:00",
            "value_sha256": hashlib.sha256(raw).hexdigest(),
            "pages": [
                {"url": "https://example.edu/registrar", "section": "Contact",
                 "near": "Registrar", "fetched_at": "2026-10-01T10:00:00+00:00",
                 "html_sha256": "b" * 64},
                {"url": "https://example.edu/directory/registrar", "section": "Registrar",
                 "fetched_at": "2026-10-01T11:00:00+00:00", "html_sha256": "c" * 64},
            ],
        }
    if fields:
        row["normalization_metadata"] = {"contact_observations": {
            "schema_version": 1, "artifact_key": "development-office-contact-evidence",
            "artifact_hash": ARTIFACT_HASH, "base_version": "prior-release", "fields": observations,
        }}
    return row


def get_facts(rows: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    service = MemoryEntityFacts(
        dataset_version="current-release", identity_hash="identity-hash", contacts=rows,
        now=lambda: NOW, entities=[{
            "id": "registrar", "kind": "office", "name": "Registrar", "aliases": [],
            "links": [{"collection": "contacts", "source_key": "directory",
                       "source_record_keys": ["office:registrar"]}],
        }],
    )
    return service.get_office_facts("registrar", fields, "current-release")


def property_for(facts: dict[str, Any], field: str) -> dict[str, Any]:
    return next(prop for prop in facts["properties"] if prop["key"] == field)


def test_fresh_phone_preserves_stale_name_original_source_and_record_count() -> None:
    row = observed_row("phones")
    result = get_facts([row], ["name", "phones"])
    assert result["mapping_version"] == "entity-facts-2"
    assert result["evidence_count"] == 1
    assert len(result["sources"]) == 2
    original, field_source = result["sources"]
    assert original["id"] == "r1"
    assert original["freshness"] == "stale"
    assert original["collected_at"] == OLD_CAPTURE
    assert field_source["id"] == "r1:contact_observation:phones"
    assert field_source["original_record_id"] == "r1"
    assert field_source["original_collected_at"] == OLD_CAPTURE
    assert field_source["original_caveats"] == original["caveats"]
    assert field_source["freshness"] == "fresh"
    assert field_source["collected_at"] == "2026-10-01T10:00:00+00:00"
    name, phone = property_for(result, "name"), property_for(result, "phones")
    assert name["values"][0]["source_ids"] == ["r1"]
    assert phone["values"][0]["source_ids"] == [field_source["id"]]
    assert phone["assertions"][0]["id"] == "r1:phones"
    assert phone["assertions"][0]["source_id"] == field_source["id"]
    assert row["collected_at"] == OLD_CAPTURE
    rendered = render_facts(result)
    assert rendered.supported is True and rendered.complete is False
    assert "Name (dated observation; current value unverified)" in rendered.text
    assert "Phones: +12015550100" in rendered.text
    citation = next(c for c in rendered.citations if c["id"] == field_source["id"])
    assert citation["original_record_id"] == "r1"
    assert citation["observation_field"] == "phones"
    assert citation["original_collected_at"] == OLD_CAPTURE


def test_email_and_rooms_refresh_independently_and_use_only_observation_page_citations() -> None:
    result = get_facts([observed_row("email", "offices")], ["email", "offices", "phones"])
    assert result["evidence_count"] == 1
    assert len(result["sources"]) == 3
    for field in ["email", "offices"]:
        source = next(s for s in result["sources"] if s.get("observation_field") == field)
        assert source["freshness"] == "fresh"
        assert source["citation_urls"] == [
            "https://example.edu/registrar", "https://example.edu/directory/registrar",
        ]
        assert property_for(result, field)["assertions"][0]["source_id"] == source["id"]
    assert property_for(result, "phones")["assertions"][0]["source_id"] == "r1"


def test_freshness_does_not_resolve_conflicts_or_override_original_validity() -> None:
    result = get_facts([
        observed_row("email", valid_until="2026-09-30"),
        observed_row(id="r2", email="other@example.edu"),
    ], ["email"])
    assert result["evidence_count"] == 2
    assert property_for(result, "email")["status"] == "conflicting"
    refreshed = next(s for s in result["sources"] if s.get("observation_field"))
    assert refreshed["freshness"] == "fresh"
    assert refreshed["validity"] == "expired"
    rendered = render_facts(result)
    assert not rendered.supported
    assert "registrar@example.edu" in rendered.text and "other@example.edu" in rendered.text


def test_metadata_absent_preserves_original_sources_and_projection_behavior() -> None:
    with_artifact_alias = observed_row()
    without_alias = deepcopy(with_artifact_alias)
    without_alias.pop("contact_observation_artifact_hash")
    result = get_facts([with_artifact_alias], ["name", "phones"])
    assert result == get_facts([without_alias], ["name", "phones"])
    assert len(result["sources"]) == result["evidence_count"] == 1
    assert property_for(result, "phones")["values"][0]["source_ids"] == ["r1"]


@pytest.mark.parametrize("field", ["email", "phones", "offices"])
def test_projection_hash_checks_raw_representation_not_normalized_value(field: str) -> None:
    row = observed_row(field)
    if field == "email":
        row["email"] += " "  # Same cleaned email, different observed raw projection.
    elif field == "phones":
        row["phones"][0]["extension"] = "123"
    else:
        row["offices"].append("D225")
    with pytest.raises(EvidenceUnavailable, match="complete raw value"):
        get_facts([row], ["name"])  # Invalid supplied metadata fails even for unrequested fields.


@pytest.mark.parametrize("alteration", [
    "artifact_missing", "artifact_mismatch", "unknown_schema", "boolean_schema", "unknown_artifact",
    "extra_top_key", "unknown_field", "empty_fields", "bad_base_version", "empty_projection",
    "naive_capture", "future_capture", "invalid_capture", "not_oldest_capture", "future_page",
    "naive_page", "empty_pages", "too_many_pages", "http_page", "credential_page", "bad_page_hash",
    "bad_value_hash", "extra_field_key", "extra_page_key", "long_section", "long_near",
])
def test_malformed_or_unbound_field_observations_fail_closed(alteration: str) -> None:
    row = observed_row("email")
    observation = row["normalization_metadata"]["contact_observations"]
    entry = observation["fields"]["email"]
    page = entry["pages"][0]
    if alteration == "artifact_missing":
        row.pop("contact_observation_artifact_hash")
    elif alteration == "artifact_mismatch":
        row["contact_observation_artifact_hash"] = "d" * 64
    elif alteration == "unknown_schema":
        observation["schema_version"] = 2
    elif alteration == "boolean_schema":
        observation["schema_version"] = True
    elif alteration == "unknown_artifact":
        observation["artifact_key"] = "unrelated-artifact"
    elif alteration == "extra_top_key":
        observation["extra"] = "ignored?"
    elif alteration == "unknown_field":
        observation["fields"]["name"] = entry
    elif alteration == "empty_fields":
        observation["fields"] = {}
    elif alteration == "bad_base_version":
        observation["base_version"] = " "
    elif alteration == "empty_projection":
        row["email"] = None
        entry["value_sha256"] = hashlib.sha256(b"null").hexdigest()
    elif alteration in {"naive_capture", "future_capture", "invalid_capture", "not_oldest_capture"}:
        entry["captured_at"] = {
            "naive_capture": "2026-10-01T10:00:00", "future_capture": "2026-10-02T10:00:00Z",
            "invalid_capture": "yesterday", "not_oldest_capture": "2026-10-01T11:00:00Z",
        }[alteration]
    elif alteration in {"future_page", "naive_page"}:
        page["fetched_at"] = ("2026-10-02T10:00:00Z" if alteration == "future_page"
                              else "2026-10-01T10:00:00")
    elif alteration in {"empty_pages", "too_many_pages"}:
        entry["pages"] = [] if alteration == "empty_pages" else [page] * 17
    elif alteration in {"http_page", "credential_page"}:
        page["url"] = ("http://example.edu/contact" if alteration == "http_page"
                       else "https://user:password@example.edu/contact")
    elif alteration == "bad_page_hash":
        page["html_sha256"] = "not-a-hash"
    elif alteration == "bad_value_hash":
        entry["value_sha256"] = "0" * 64
    elif alteration == "extra_field_key":
        entry["fresh"] = True
    elif alteration == "extra_page_key":
        page["confidence"] = 1
    elif alteration == "long_section":
        page["section"] = "x" * 1_001
    else:
        page["near"] = "x" * 2_001
    with pytest.raises(EvidenceUnavailable):
        get_facts([row], ["email"])


def test_cross_language_raw_hash_preserves_unicode_null_array_order_and_nested_keys() -> None:
    row = {"phone": None, "phones": [
        {"type": "téléphone", "number": "+12015550100", "extension": None},
        {"number": "+12015550101", "type": "fax"},
    ]}
    assert _projection_hash(row, "phones") == (
        "b61b2bba806321050153bbf5a93ed19a1ca0b93ae528c932b178bdb7bb01e537"
    )
    reversed_row = {**row, "phones": list(reversed(row["phones"]))}  # type: ignore[arg-type]
    assert _projection_hash(reversed_row, "phones") != _projection_hash(row, "phones")
    with pytest.raises(EvidenceUnavailable, match="raw projection"):
        _projection_hash({"phone": None, "phones": [{"extension": 123}]}, "phones")


def test_derived_source_cannot_point_to_another_original_record_or_field() -> None:
    row = observed_row("phones")
    result = get_facts([row], ["phones"])
    result["sources"][1]["original_record_id"] = "invented"
    with pytest.raises(EvidenceUnavailable, match="original record and exact field"):
        canonical_properties([row], ["phones"], result["sources"])


def test_an_observation_older_than_the_record_does_not_demote_a_fresh_record() -> None:
    row = observed_row("phones", collected_at="2026-10-01T11:30:00+00:00")
    seen = row["normalization_metadata"]["contact_observations"]["fields"]["phones"]
    seen["captured_at"] = "2026-09-25T10:00:00+00:00"
    for page in seen["pages"]:
        page["fetched_at"] = "2026-09-25T10:00:00+00:00"
    result = get_facts([row], ["phones"])
    assert [source["id"] for source in result["sources"]] == ["r1"]
    assert result["sources"][0]["freshness"] == "fresh"
    assert property_for(result, "phones")["assertions"][0]["source_id"] == "r1"
    rendered = render_facts(result)
    assert rendered.supported and "dated observation" not in rendered.text


def test_an_observation_exactly_as_recent_as_the_record_adds_nothing() -> None:
    row = observed_row("phones", collected_at="2026-10-01T10:00:00+00:00")
    result = get_facts([row], ["phones"])  # The observation was captured at the same instant.
    assert [source["id"] for source in result["sources"]] == ["r1"]


def test_an_invalid_older_observation_still_fails_the_read() -> None:
    row = observed_row("phones", collected_at="2026-10-01T11:30:00+00:00")
    row["normalization_metadata"]["contact_observations"]["fields"]["phones"][
        "value_sha256"] = "f" * 64
    with pytest.raises(EvidenceUnavailable):
        get_facts([row], ["phones"])
