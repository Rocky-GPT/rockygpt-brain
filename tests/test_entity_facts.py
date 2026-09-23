"""Canonical facts are shared semantics, not destructive source-row deduplication."""

from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException

import test_projection
from rockygpt_brain.retrieval.entity_facts import (
    EntityFactProjection,
    EntityFacts,
    FactProperty,
    _lineage,
    canonical_properties,
)
from rockygpt_brain.retrieval.projection import Projection, valid_value
from rockygpt_brain.retrieval.projection_models import Assertion, Property, SourceRecord
from test_projection import ENTITY_ID, Fixture

fixture = test_projection.fixture


def source(identifier: str, **kwargs: Any) -> SourceRecord:
    return SourceRecord(
        id=identifier,
        collection="contacts",
        row_id=identifier,
        source_key="directory",
        source_record_key=identifier,
        source_url="https://example.edu",
        collected_at=None,
        valid_from=None,
        valid_until=None,
        freshness="unknown",
        limitations=[],
        **kwargs,
    )


def prop(key: str, value: Any, source_id: str) -> Property:
    return Property(
        key=key,
        label=key,
        value_type="text",
        assertions=[
            Assertion(
                id=f"{source_id}#{key}",
                source_id=source_id,
                value=value,
                field_path=[key],
                limitations=[],
            )
        ],
    )


def test_same_fact_one_value_many_evidence_records_no_independence_claim() -> None:
    properties = [prop("email", "person@example.edu", s) for s in ["a", "b"]]
    before = copy.deepcopy(properties)
    (fact,) = canonical_properties(properties, [source("a"), source("b")])
    assert fact.status == "known"
    assert len(fact.values) == 1
    assert fact.values[0].supporting_evidence_ids == ["a", "b"]
    assert fact.values[0].evidence_count == 2
    assert fact.values[0].assertion_ids == ["a#email", "b#email"]
    assert "source_count" not in fact.model_dump()
    assert properties == before


def test_phone_office_aliases_preserve_raw_evidence_and_extensions() -> None:
    fields = [
        prop("phone", "(201) 684-7392", "a"),
        prop("phones", [{"number": "201-684-7392"}], "a"),
        prop("phone", "201.684.7392", "b"),
        prop("office", "ASB-107", "a"),
        prop("offices", ["ASB-107"], "b"),
    ]
    facts = {f.key: f for f in canonical_properties(fields, [source("a"), source("b")])}
    assert set(facts) == {"phones", "offices"}
    assert facts["phones"].status == "known" and len(facts["phones"].values) == 1
    assert facts["phones"].values[0].value == [{"number": "201-684-7392"}]
    assert facts["phones"].values[0].evidence_count == 2
    assert facts["phones"].assertions[0].value == "(201) 684-7392"
    assert facts["offices"].values[0].value == ["ASB-107"]
    extension = prop("phone", "201-684-7392 ext. 123", "c")
    (fact,) = canonical_properties([fields[0], extension], [source("a"), source("c")])
    assert fact.status == "conflicting"  # different complete phone values, never discard extension
    assert fact.values[1].value == [{"number": "201-684-7392", "extension": "123"}]


def test_missing_values_do_not_contradict_known_and_false_zero_are_not_missing() -> None:
    (fact,) = canonical_properties(
        [prop("email", None, "a"), prop("email", "x", "b")], [source("a"), source("b")]
    )
    assert fact.status == "known" and len(fact.values) == 2
    (fact,) = canonical_properties(
        [prop("flag", False, "a"), prop("flag", 0, "b")], [source("a"), source("b")]
    )
    assert fact.status == "conflicting" and len(fact.values) == 2
    (preference,) = canonical_properties([prop("prefers_email", False, "a")], [source("a")])
    assert preference.status == "unknown" and preference.values[0].value is None
    assert preference.assertions[0].value is False


def test_extension_only_and_labeled_multiple_phones_have_one_canonical_path() -> None:
    assert valid_value([{"extension": "7609"}], "phone_list")
    assert not valid_value([{"type": "office"}], "phone_list")
    for display, structured in [
        ("Ext. 7609", [{"extension": "7609"}]),
        (
            "(201) 684-7392 (Office) / (201) 684-7393 (Cell)",
            [
                {"number": "201-684-7392", "type": "office"},
                {"number": "201-684-7393", "type": "cell"},
            ],
        ),
    ]:
        (fact,) = canonical_properties(
            [prop("phone", display, "a"), prop("phones", structured, "a")], [source("a")]
        )
        assert fact.status == "known" and len(fact.values) == 1
        assert fact.values[0].value == structured
        assert fact.values[0].evidence_count == 1


def test_conflicts_are_explicit_but_disjoint_date_claims_are_not_conflicts() -> None:
    properties = [prop("email", "a@example.edu", "a"), prop("email", "b@example.edu", "b")]
    sources = [source("a"), source("b")]
    assert canonical_properties(properties, sources)[0].status == "conflicting"
    sources[0].valid_until = "2026-09-01"
    sources[1].valid_from = "2026-09-02"
    assert canonical_properties(properties, sources)[0].status == "multiple"
    sources[1].valid_from = "2026-09-01"
    assert canonical_properties(properties, sources)[0].status == "conflicting"


@pytest.mark.parametrize(
    ("raw", "number", "note"),
    [
        ("(201) 684-7293 (use email instead)", "201-684-7293", "use email instead"),
        ("(201) 684-7852 (best to use e-mail)", "201-684-7852", "best to use e-mail"),
    ],
)
def test_phone_email_notes_qualify_same_number_without_false_conflict(
    raw: str,
    number: str,
    note: str,
) -> None:
    properties = [prop("phone", raw, "a"), prop("phones", [{"number": number}], "b")]
    (fact,) = canonical_properties(properties, [source("a"), source("b")])
    assert fact.status == "known" and len(fact.values) == 1
    assert fact.values[0].value == [{"number": number}]
    assert fact.assertions[0].value == raw
    assert fact.assertions[0].limitations == [f"Source phone note: {note}"]


def test_json_equality_is_conservative() -> None:
    sources = [source("a"), source("b")]
    (fact,) = canonical_properties(
        [prop("x", {"a": 1, "b": 2}, "a"), prop("x", {"b": 2, "a": 1}, "b")], sources
    )
    assert len(fact.values) == 1
    for left, right in [([1, 2], [2, 1]), ("x", "X"), ("1", 1), ("dept", "Dept")]:
        assert (
            canonical_properties([prop("x", left, "a"), prop("x", right, "b")], sources)[0].status
            == "conflicting"
        )


def test_support_contract_rejects_lost_or_fabricated_evidence() -> None:
    (fact,) = canonical_properties([prop("email", "x", "a")], [source("a")])
    payload = fact.model_dump()
    payload["values"][0]["supporting_evidence_ids"] = ["fabricated"]
    with pytest.raises(ValueError, match="support"):
        FactProperty.model_validate(payload)
    payload = fact.model_dump()
    payload["values"][0]["assertion_ids"] = []
    with pytest.raises(ValueError, match="Every assertion"):
        FactProperty.model_validate(payload)


def test_exact_faculty_derivation_is_not_independent_corroboration() -> None:
    sources = [source("contact"), source("faculty")]
    sources[0].source_key = "faculty"
    sources[0].source_record_key = "faculty:rikki-abzug:business"
    sources[1].collection = "faculty"
    properties = [
        prop("name", "Rikki Abzug", "faculty"),
        prop("school", "Business", "faculty"),
        prop("email", "rabzug@example.edu", "faculty"),
        prop("email", "rabzug@example.edu", "contact"),
    ]
    annotated = _lineage(properties, sources)
    assert annotated[0].derived_from_source_id == "faculty"
    assert sources[0].derived_from_source_id is None
    properties[-1].assertions[0].value = "different@example.edu"
    assert _lineage(properties, sources)[0].derived_from_source_id is None


def test_projection_v3_reuses_exact_readers_and_preserves_contexts(fixture: Fixture) -> None:
    data, snapshot, records = fixture
    before = copy.deepcopy(records)
    reader = EntityFacts(data, snapshot)
    result = reader.build(ENTITY_ID, include_records=True, limit=2)
    assert result.schema_version == 3 and result.projection_version == "entity-facts-1"
    assert {p.key: p for p in result.properties}["email"].status == "conflicting"
    menu = result.record_groups[0]
    assert menu.returned == 2 and menu.total == 3
    assert len({r.id for r in menu.records}) == 2
    assert menu.records[0].properties[3].key == "vegan"
    vegan = next(p for p in menu.records[0].properties if p.key == "vegan")
    assert vegan.values[0].value is False and vegan.status == "known"
    allergens = next(p for p in menu.records[0].properties if p.key == "allergens")
    assert allergens.values[0].value is None and allergens.assertions[0].value == []
    assert records == before
    assert set(reader.source_records) == {s.id for s in result.sources}
    EntityFactProjection.model_validate_json(result.model_dump_json())
    second = EntityFacts(data, snapshot).build(
        ENTITY_ID, include_records=True, group=menu.key, cursor=menu.next_cursor, limit=2
    )
    assert second.properties == [] and len(second.record_groups[0].records) == 1
    with pytest.raises(HTTPException) as caught:
        Projection(data, snapshot).build(UUID(ENTITY_ID), menu.key, {}, 2, menu.next_cursor)
    assert caught.value.status_code == 409


def test_property_only_lookup_does_not_read_context_collections(fixture: Fixture) -> None:
    data, snapshot, _ = fixture
    reader = EntityFacts(data, snapshot)
    result = reader.build(ENTITY_ID)
    assert result.record_groups == []
    assert {s.collection for s in result.sources} == {"contacts"}


def test_explicit_retirement_is_status_not_a_competing_title() -> None:
    a, b = source("a"), source("b")
    b.collection = "faculty"
    properties = [
        prop("title", "Professor of History", "a"),
        prop("status", "retired", "a"),
        prop("title", "Professor of History - Retired", "b"),
    ]
    facts = {p.key: p for p in canonical_properties(properties, [a, b])}
    assert facts["title"].status == "known"
    assert facts["title"].values[0].value == "Professor of History"
    assert facts["status"].values[0].value == "retired"
    assert facts["status"].values[0].supporting_evidence_ids == ["a", "b"]
    assert facts["status"].assertions[1].field_path == ["title"]
    assert facts["status"].assertions[1].value == "Professor of History - Retired"
    for title in ["Professor Emeritus", "Professor of Retirement Studies"]:
        facts = canonical_properties([prop("title", title, "b")], [b])
        assert [p.key for p in facts] == ["title"]
    facts = {p.key: p for p in canonical_properties([prop("title", "Retired", "b")], [b])}
    assert facts["title"].status == "unknown" and facts["status"].values[0].value == "retired"


def test_source_cleanup_and_date_only_events_are_shared_semantics() -> None:
    a, b = source("a"), source("b")
    b.collection = "faculty"
    (fact,) = canonical_properties(
        [prop("name", "Yolanda del Amo", "a"), prop("name", "Yolanda del\u00a0Amo", "b")], [a, b]
    )
    assert len(fact.values) == 1 and fact.assertions[1].value.endswith("del\u00a0Amo")
    a.collection = "events"
    facts = {
        p.key: p
        for p in canonical_properties(
            [prop("starts_at", "2026-10-01T00:00:00", "a"), prop("start_time", None, "a")], [a]
        )
    }
    assert facts["starts_at"].value_type == "date_or_datetime"
    assert facts["starts_at"].values[0].value == "2026-10-01"
    assert facts["starts_at"].assertions[0].value == "2026-10-01T00:00:00"
    assert "start time is unavailable" in facts["starts_at"].assertions[0].limitations[0]


def test_office_codes_are_formatted_but_named_locations_keep_their_meaning() -> None:
    a, b = source("a"), source("b")
    (fact,) = canonical_properties(
        [prop("office", "ASB312 / ASB 314", "a"), prop("offices", ["ASB-312", "ASB-314"], "b")],
        [a, b],
    )
    assert fact.status == "known" and len(fact.values) == 1
    (fact,) = canonical_properties([prop("office", "Library / Service Desk", "a")], [a])
    assert fact.values[0].value == ["Library / Service Desk"]


def test_an_email_mention_does_not_become_a_preference() -> None:
    fields = [
        prop("prefers_email", True, "a"),
        prop("preferred_contact", "email", "a"),
        prop("contact_note", "email unavailable", "a"),
    ]
    facts = {p.key: p for p in canonical_properties(fields, [source("a")])}
    assert facts["prefers_email"].status == facts["preferred_contact"].status == "unknown"
    fields[-1].assertions[0].value = "use email instead"
    facts = {p.key: p for p in canonical_properties(fields, [source("a")])}
    assert facts["prefers_email"].values[0].value is True
    assert facts["preferred_contact"].values[0].value == "email"
