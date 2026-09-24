"""Independent boundaries for linked profiles and their evidence admission."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.governance.evidence import bounded_result
from rockygpt_brain.retrieval.data import CampusData
from rockygpt_brain.retrieval.profiles import ProfileQuery
from test_engine import answer, review, tools
from test_evidence import expand_records

NOW = datetime(2026, 9, 21, 16, tzinfo=UTC)
ENTITY_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


def link(collection: str, source: str, *keys: str) -> dict[str, Any]:
    return {"collection": collection, "source_key": source, "source_record_keys": list(keys)}


def identity(*links: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {
        "id": ENTITY_ID,
        "kind": "office",
        "name": "Center for Student Involvement",
        "aliases": ["CSI"],
        "links": list(links),
        **changes,
    }


def source(key: str) -> dict[str, Any]:
    return {
        "id": key,
        "source_key": key,
        "title": key,
        "canonical_url": "https://www.ramapo.edu/" + key + "/",
        "trust_tier": "official_primary",
        "freshness_sla_hours": 24,
        "provenance_status": "success",
        "completed_at": NOW,
    }


def row(source_key: str, record_key: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": source_key + "-row",
        "source_id": source_key,
        "source_record_key": record_key,
        "name": "Center for Student Involvement",
        "collected_at": NOW,
        "valid_from": None,
        "valid_until": None,
        "total": 1,
        **fields,
    }


def repository(
    entities: list[dict[str, Any]], records_by_source: dict[str, list[dict[str, Any]] | Exception]
) -> CampusData:
    data = CampusData("postgresql://unused", NOW)
    data.dataset = {"id": "pinned-dataset", "version": "published-one", "activated_at": NOW}
    data.sources = {key: source(key) for key in records_by_source}
    data._artifacts["campus-identities"] = {"schema_version": 1, "entities": deepcopy(entities)}

    def fetch(query: Any, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        statement = query.as_string()
        assert "t.dataset_version_id=%s::uuid" in statement
        assert "s.source_key=%s" in statement
        assert "t.source_record_key=ANY(%s)" in statement
        assert params[0] == data.dataset["id"]
        result = records_by_source[params[1]]
        if isinstance(result, Exception):
            raise result
        assert {value["source_record_key"] for value in result} <= set(params[2])
        return deepcopy(result)

    data._fetch = Mock(side_effect=fetch)  # type: ignore[method-assign]
    return data


def test_an_alias_colliding_with_another_canonical_name_requires_clarification() -> None:
    data = repository(
        [identity(link("contacts", "directory", "office:csi")),
         identity(link("contacts", "directory", "office:other"),
                  id=OTHER_ID, name="CSI", aliases=[])], {}
    )
    output = data.lookup_profile(ProfileQuery(entity="csi", include=["contact", "hours"]))
    assert output["resolution"]["status"] == "ambiguous"
    assert {candidate["id"] for candidate in output["resolution"]["candidates"]} == {
        ENTITY_ID, OTHER_ID,
    }
    assert output["records"] == []
    assert data._fetch.call_count == 0  # type: ignore[attr-defined]


def test_a_failed_hours_link_keeps_contact_evidence_and_does_not_fallback_by_name() -> None:
    data = repository(
        [identity(link("contacts", "directory", "office:csi"),
                  link("campus_hours", "hours", "csi:Monday"))],
        {
            "directory": [row("directory", "office:csi", phone="201-684-1111")],
            "hours": TimeoutError("unavailable source"),
        },
    )
    output = data.lookup_profile(ProfileQuery(entity="CSI", include=["contact", "hours"]))
    assert output["status"] == "ok"
    assert output["components"]["contact"]["fields"]["phone"] == "published"
    assert output["components"]["hours"]["status"] == "unavailable"
    assert [record["collection"] for record in output["records"]] == ["contacts"]
    assert data._fetch.call_count == 2  # type: ignore[attr-defined]


def test_conflicting_phones_do_not_erase_a_consistent_email() -> None:
    data = repository(
        [identity(link("contacts", "directory", "office:csi"),
                  link("contacts", "office-page", "csi-contact"))],
        {
            "directory": [row("directory", "office:csi", phone="201-684-1111",
                              email="csi@example.edu")],
            "office-page": [row("office-page", "csi-contact", phone="201-684-2222",
                                email="csi@example.edu")],
        },
    )
    output = data.lookup_profile(ProfileQuery(entity="CSI", include=["contact"]))
    contact = output["components"]["contact"]
    assert contact["fields"]["phone"] == "conflict"
    assert contact["fields"]["email"] == "published"
    assert set(contact["conflicts"]) == {"phone"}
    assert len(contact["conflicts"]["phone"]) == 2
    assert {record["fields"]["email"] for record in output["records"]} == {"csi@example.edu"}
    assert all("phone" in " ".join(record["limitations"]) for record in output["records"])


def test_a_missing_field_is_not_a_competing_value() -> None:
    data = repository(
        [identity(link("contacts", "directory", "office:csi"),
                  link("contacts", "office-page", "csi-contact"))],
        {
            "directory": [row("directory", "office:csi", phone="201-684-1111")],
            "office-page": [row("office-page", "csi-contact", phone=None,
                                email="csi@example.edu")],
        },
    )
    output = data.lookup_profile(ProfileQuery(entity="CSI", include=["contact"]))
    assert output["components"]["contact"]["conflicts"] == {}
    assert output["components"]["contact"]["fields"]["phone"] == "published"
    assert output["components"]["contact"]["fields"]["email"] == "published"
    assert output["components"]["contact"]["fields"]["office"] == "not_published"


def test_dated_hours_do_not_override_a_different_sources_disagreement() -> None:
    data = repository(
        [identity(link("campus_hours", "hours", "csi:Monday"),
                  link("campus_hours", "office-page", "csi-special"))],
        {
            "hours": [row("hours", "csi:Monday", day="Monday", schedule="08:00 - 00:00",
                          hours=[{"open": "08:00", "close": "00:00", "close_day_offset": 1}])],
            "office-page": [row("office-page", "csi-special", day="Monday",
                                valid_from="2026-09-21", valid_until="2026-09-21",
                                schedule="08:00 - 17:00",
                                hours=[{"open": "08:00", "close": "17:00",
                                        "close_day_offset": 0}])],
        },
    )
    output = data.lookup_profile(ProfileQuery(entity="CSI", include=["hours"],
                                             date=date(2026, 9, 21)))
    assert len(output["records"]) == 2
    assert output["components"]["hours"]["fields"]["schedule"] == "conflict"
    assert output["components"]["hours"]["fields"]["hours"] == "conflict"
    assert all(record["fields"]["availability_scope"] == "unspecified"
               for record in output["records"])
    assert all("phone-answering hours" in " ".join(record["limitations"])
               for record in output["records"])


def test_schedules_one_source_lists_apart_are_not_a_conflict() -> None:
    def hours(source_key: str, key: str, name: str, schedule: str) -> dict[str, Any]:
        return row(source_key, key, id=key, name=name, day="Monday", schedule=schedule)

    main = hours("hours", "main", "Library (Main Building)", "8:00am-6:00pm")
    desk = hours("hours", "desk", "Research Help Desk", "Hours unavailable")
    # Only case and spacing set this name apart from the main building's.
    variant = hours("hours", "variant", "library (main building) ", "8:00am-5:00pm")

    def profile(*rows: dict[str, Any]) -> dict[str, Any]:
        by_source: dict[str, list[dict[str, Any]]] = {}
        for value in rows:
            by_source.setdefault(value["source_id"], []).append(value)
        collections = {"dining": "dining_hours"}
        links = [link(collections.get(source_key, "campus_hours"), source_key,
                      *[value["source_record_key"] for value in values])
                 for source_key, values in by_source.items()]
        data = repository([identity(*links)], dict(by_source))
        data._artifacts["dining-hours"] = None  # No published meal periods.
        output: dict[str, Any] = data.lookup_profile(
            ProfileQuery(entity="CSI", include=["hours"], date=date(2026, 9, 21)))
        return output

    def disagree(output: dict[str, Any]) -> set[str]:
        return {record["id"].removesuffix(":2026-09-21") for record in output["records"]
                if any("disagree" in note for note in record["limitations"])}

    output = profile(main, desk)
    component = output["components"]["hours"]
    assert (component["conflicts"], disagree(output)) == ({}, set())
    assert component["fields"] == {"hours": "not_published", "schedule": "published",
                                   "periods": "not_published"}
    # One schedule still disagrees with itself, and only its records say so.
    output = profile(main, desk, variant)
    label = "campus_hours.Library (Main Building).schedule"
    component = output["components"]["hours"]
    assert set(component["conflicts"]) == {label}
    assert (component["fields"][label], component["fields"]["schedule"]) == (
        "conflict", "published")
    assert disagree(output) == {"campus_hours:main", "campus_hours:variant"}
    assert {record["id"]: record["coverage"]["fields"]["schedule"]
            for record in output["records"]} == {
        "campus_hours:main:2026-09-21": "conflict", "campus_hours:variant:2026-09-21": "conflict",
        "campus_hours:desk:2026-09-21": "published"}
    # Another source's name shows nothing about which schedule it is, so it is compared
    # with every schedule, as it was before.
    other = hours("office-page", "other", "Library", "8:00am-5:00pm")
    output = profile(main, desk, other)
    assert set(output["components"]["hours"]["conflicts"]) == {"schedule"}
    assert disagree(output) == {"campus_hours:main", "campus_hours:desk", "campus_hours:other"}
    # A record without a name could be either schedule, so it is compared with both.
    output = profile(main, desk, hours("hours", "untitled", "", "9:00am-1:00pm"))
    assert set(output["components"]["hours"]["conflicts"]) == {"schedule"}
    assert disagree(output) == {"campus_hours:main", "campus_hours:desk", "campus_hours:untitled"}
    # One schedule per collection keeps the collection as its label.
    output = profile(main, variant, hours("dining", "cafe", "Cafe", "7:00am-9:00pm"))
    assert set(output["components"]["hours"]["conflicts"]) == {"campus_hours.schedule"}
    assert disagree(output) == {"campus_hours:main", "campus_hours:variant"}


def test_rename_and_refresh_keep_canonical_id_but_use_new_release_evidence() -> None:
    first = repository(
        [identity(link("contacts", "directory", "office:csi"))],
        {"directory": [row("directory", "office:csi", phone="201-684-1111")]},
    )
    second = repository(
        [identity(link("contacts", "directory", "office:student-engagement"),
                  name="Student Engagement", aliases=["CSI", "Center for Student Involvement"])],
        {"directory": [row("directory", "office:student-engagement", id="new-row",
                           name="Student Engagement", phone="201-684-2222")]},
    )
    second.dataset = {"id": "new-pinned-dataset", "version": "published-two", "activated_at": NOW}
    query = ProfileQuery.model_validate({"entity_id": ENTITY_ID, "include": ["contact"]})
    old, new = first.lookup_profile(query), second.lookup_profile(query)
    assert old["resolution"]["entity"]["id"] == new["resolution"]["entity"]["id"] == ENTITY_ID
    assert old["records"][0]["id"] != new["records"][0]["id"]
    assert new["records"][0]["entity_id"] == "directory:office:student-engagement"
    assert new["records"][0]["canonical_entity_id"] == ENTITY_ID
    assert new["records"][0]["fields"]["phone"] == "201-684-2222"
    assert new["dataset_version"] == "published-two"
    assert second._fetch.call_args.args[1] == (  # type: ignore[attr-defined]
        "new-pinned-dataset", "directory", ["office:student-engagement"]
    )


def test_one_source_record_cannot_prove_two_separate_identities() -> None:
    shared = link("contacts", "directory", "office:csi")
    data = repository(
        [identity(shared), identity(shared, id=OTHER_ID, name="Student Center", aliases=[])],
        {"directory": [row("directory", "office:csi", phone="201-684-1111")]},
    )
    output = data.lookup_profile(ProfileQuery(entity="CSI", include=["contact"]))
    assert output["status"] == "unavailable"
    assert output["reason"] == "invalid_identity_registry"
    assert output["records"] == []
    assert data._fetch.call_count == 0  # type: ignore[attr-defined]


@pytest.mark.parametrize("count", [0, 1])
def test_context_truncation_cannot_leave_complete_profile_claims(count: int) -> None:
    output: dict[str, Any] = {
        "status": "ok",
        "dataset_version": "pinned-release",
        "records": [{"id": "contacts:one"}, {"id": "campus_hours:two"}],
        "truncated": False,
        "total_matches": 2,
        "components": {
            "contact": {"status": "available", "evidence_ids": ["contacts:one"]},
            "hours": {"status": "available", "evidence_ids": ["campus_hours:two"]},
        },
    }
    admitted = bounded_result(output, lambda candidate: len(candidate.get("records", [])) <= count)
    assert len(admitted["records"]) == count
    assert admitted["truncated"] is True
    assert admitted["reason"] == "retrieval_delivery_limit"
    assert "components" not in admitted
    assert output["components"]["hours"]["evidence_ids"] == ["campus_hours:two"]


def review_repository() -> CampusData:
    return repository(
        [identity(link("contacts", "directory", "office:csi"),
                  link("contacts", "office-page", "csi-contact"),
                  link("campus_hours", "hours", "csi:Monday"),
                  aliases=["Student Activities"])],
        {
            "directory": [row("directory", "office:csi", phone="201-684-1111",
                              email="csi@example.edu")],
            "office-page": [row("office-page", "csi-contact", phone="201-684-2222",
                                email="csi@example.edu")],
            "hours": [row("hours", "csi:Monday", day="Monday", schedule="08:00 - 00:00",
                          hours=[{"open": "08:00", "close": "00:00", "close_day_offset": 1}])],
        },
    )


def profile_call() -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call", name="lookup_profile", call_id="profile",
        arguments=json.dumps({"entity": "Student Activities", "include": ["contact", "hours"]}),
    )


def test_profile_review_receives_identity_resolution_conflicts_and_hours_scope() -> None:
    client = Mock()
    client.create.side_effect = [
        tools(profile_call()),
        answer("The published email is csi@example.edu.",
               "campus_fact", ["contacts:directory-row"]),
        review("supported"),
    ]
    result = run_turn(
        [ChatMessage(role="user", content="What is Student Activities' contact info and hours?")],
        client=client, data=review_repository(), model="test", now=NOW,
    )
    assert result["metrics"]["reviewCalls"] == 1
    assert result["status"] == "answered"
    review_input = json.loads(client.create.call_args_list[-1].kwargs["input"])
    records = expand_records(review_input["evidence"])
    assert {record["canonical_entity_id"] for record in records} == {ENTITY_ID}
    contacts = [record for record in records if record["collection"] == "contacts"]
    assert all("disagree on phone" in " ".join(record["limitations"]) for record in contacts)
    assert {record["fields"]["email"] for record in contacts} == {"csi@example.edu"}
    hours = next(record for record in records if record["collection"] == "campus_hours")
    assert hours["fields"]["availability_scope"] == "unspecified"
    assert "never infer phone-answering hours" in " ".join(hours["limitations"])
    coverage = review_input["retrieval_coverage"][0]
    assert coverage["tool"] == "lookup_profile"
    assert coverage["resolution"]["status"] == "matched"
    assert coverage["resolution"]["entity"]["id"] == ENTITY_ID


def test_profile_draft_cannot_bypass_review_of_unsupported_staff_availability() -> None:
    client = Mock()
    unsupported = "Staff answer the Student Activities phone until midnight."
    client.create.side_effect = [
        tools(profile_call()),
        answer(unsupported, "campus_fact",
               ["contacts:directory-row", "campus_hours:hours-row:2026-09-21"]),
        review("unsupported_claim", unverified_premises=["Phone answering hours are unspecified"]),
    ]
    result = run_turn(
        [ChatMessage(role="user", content="Can I call Student Activities until midnight?")],
        client=client, data=review_repository(), model="test", now=NOW,
    )
    assert result["metrics"]["reviewCalls"] == 1
    assert result["metrics"]["fallbackUsed"] is True
    assert result["status"] == "unavailable"
    assert unsupported not in result["answer"]
