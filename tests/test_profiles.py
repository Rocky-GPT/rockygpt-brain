"""Profiles link verified identities without combining source authority or availability."""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.tools import tool_definitions
from rockygpt_brain.governance.evidence import bounded_result
from rockygpt_brain.retrieval.data import CampusData
from rockygpt_brain.retrieval.profiles import ProfileQuery
from test_engine import answer, review, tools

NOW = datetime(2026, 9, 21, 16, tzinfo=UTC)
ENTITY_ID = "9f4a8a53-67a1-4ce4-b4da-5d19630f135b"
IDENTITY = {
    "id": ENTITY_ID,
    "kind": "office",
    "name": "Example Center",
    "aliases": ["EC"],
    "links": [
        {"collection": "contacts", "source_key": "directory", "source_record_keys": ["office:ec"]},
        {"collection": "campus_hours", "source_key": "hours", "source_record_keys": ["ec:Monday"]},
    ],
}


def repository() -> CampusData:
    data = CampusData("postgresql://unused", NOW)
    data.dataset = {"id": "dataset-one", "version": "test-release"}
    data.sources = {
        key: {
            "id": key,
            "source_key": key,
            "title": f"Published {key}",
            "canonical_url": f"https://example.edu/{key}",
            "trust_tier": "official_primary",
            "freshness_sla_hours": 168,
            "provenance_status": "success",
        }
        for key in ("directory", "hours")
    }
    data._artifacts["campus-identities"] = {
        "schema_version": 1,
        "entities": [copy.deepcopy(IDENTITY)],
    }
    # Releases before the data repository published these artifacts had neither.
    data._artifacts["catalog-course-identities"] = None
    data._artifacts["program-requirement-groups"] = None
    return data


def rows() -> tuple[dict[str, Any], dict[str, Any]]:
    common = {
        "name": "Example Center",
        "collected_at": NOW,
        "total": 1,
        "valid_from": None,
        "valid_until": None,
    }
    return (
        {
            **common,
            "id": "contact",
            "source_id": "directory",
            "source_record_key": "office:ec",
            "phone": "201-555-0100",
            "email": "ec@example.edu",
        },
        {
            **common,
            "id": "hours",
            "source_id": "hours",
            "source_record_key": "ec:Monday",
            "day": "Monday",
            "schedule": "8:00 AM - midnight the following day",
            "hours": [{"open": "08:00", "close": "00:00", "close_day_offset": 1}],
        },
    )


def attach_rows(data: CampusData, contact: dict[str, Any], hours: dict[str, Any]) -> Mock:
    fetch = Mock(
        side_effect=lambda _sql, params: [contact] if params[1] == "directory" else [hours]
    )
    data._fetch = fetch  # type: ignore[method-assign]
    return fetch


def test_profile_resolves_alias_and_uses_exact_pinned_keys_with_original_sources() -> None:
    data = repository()
    contact, hours = rows()
    hours["collected_at"] = NOW - timedelta(days=40)
    fetch = attach_rows(data, contact, hours)
    result = data.lookup_profile(ProfileQuery(entity="  ec  ", include=["contact", "hours"]))
    assert result["resolution"]["entity"]["id"] == ENTITY_ID
    assert result["total_matches"] == 2
    first, second = result["records"]
    assert first["entity_id"] == "directory:office:ec"
    assert first["canonical_entity_id"] == second["canonical_entity_id"] == ENTITY_ID
    assert first["url"] != second["url"]
    assert first["freshness"] == "fresh" and second["freshness"] == "stale"
    assert second["collected_at"] == hours["collected_at"].isoformat()
    assert second["fields"]["service_date"] == "2026-09-21"
    assert second["fields"]["hours"][0]["close_day_offset"] == 1
    assert second["fields"]["availability_scope"] == "unspecified"
    assert any("phone-answering" in limit for limit in second["limitations"])
    assert fetch.call_args_list[0].args[1] == ("dataset-one", "directory", ["office:ec"])
    assert fetch.call_args_list[1].args[1] == ("dataset-one", "hours", ["ec:Monday"])
    assert "LIKE" not in str(fetch.call_args_list[0].args[0])


def test_renames_and_refresh_row_ids_keep_persistent_identity() -> None:
    data = repository()
    contact, hours = rows()
    attach_rows(data, contact, hours)
    before = data.lookup_profile(ProfileQuery(entity="EC", include=["contact"]))
    identity = data._artifacts["campus-identities"]["entities"][0]
    identity.update(name="Renamed Center", aliases=["Example Center", "EC"])
    contact.update(id="new-row-id", name="Renamed Center")
    after = data.lookup_profile(ProfileQuery(entity_id=UUID(ENTITY_ID), include=["contact"]))
    assert before["resolution"]["entity"]["id"] == after["resolution"]["entity"]["id"]
    assert before["records"][0]["id"] != after["records"][0]["id"]


def test_ambiguity_and_no_match_never_fetch_source_records() -> None:
    data = repository()
    other = copy.deepcopy(IDENTITY)
    other.update(
        id="a8306d1b-0319-477a-88fa-c32e2bab5f4e",
        name="Another Center",
        links=[
            {"collection": "contacts", "source_key": "directory", "source_record_keys": ["other"]}
        ],
    )
    data._artifacts["campus-identities"]["entities"].append(other)
    fetch = Mock()
    data._fetch = fetch  # type: ignore[method-assign]
    ambiguous = data.lookup_profile(ProfileQuery(entity="EC", include=["contact", "hours"]))
    assert ambiguous["resolution"]["status"] == "ambiguous"
    assert len(ambiguous["resolution"]["candidates"]) == 2
    assert ambiguous["records"] == []
    missing = data.lookup_profile(ProfileQuery(entity="Unlinked office", include=["contact"]))
    assert missing["resolution"]["status"] == "no_match"
    fetch.assert_not_called()


def test_a_name_whose_matches_are_all_its_qualified_lines_returns_each_line() -> None:
    # "Public Safety" names both the Emergency and Non-Emergency lines: answer with both.
    data = repository()
    emergency = copy.deepcopy(IDENTITY)
    emergency.update(name="Example Center (Emergency)", aliases=["Example Center", "EC"])
    other = copy.deepcopy(IDENTITY)
    other.update(
        id="a8306d1b-0319-477a-88fa-c32e2bab5f4e",
        name="Example Center (Non-Emergency)",
        aliases=["Example Center", "EC"],
        links=[
            {"collection": "contacts", "source_key": "directory", "source_record_keys": ["other"]}
        ],
    )
    data._artifacts["campus-identities"]["entities"] = [emergency, other]
    contact, hours = rows()
    second = {**contact, "id": "contact-2", "source_record_key": "other", "phone": "201-555-0199"}
    data._fetch = Mock(  # type: ignore[method-assign]
        side_effect=lambda _sql, params: [second] if params[2] == ["other"] else [contact]
    )
    result = data.lookup_profile(ProfileQuery(entity="Example Center", include=["contact"]))
    assert result["resolution"]["status"] == "variants"
    assert {item["name"] for item in result["resolution"]["candidates"]} == {
        "Example Center (Emergency)", "Example Center (Non-Emergency)"}
    assert {record["fields"]["phone"] for record in result["records"]} == {
        "201-555-0100", "201-555-0199"}
    assert [item["entity"]["name"] for item in result["variants"]] == [
        "Example Center (Emergency)", "Example Center (Non-Emergency)"]
    # An alias both lines share names both too, as "Campus Police" does.
    shared = data.lookup_profile(ProfileQuery(entity="EC", include=["contact"]))
    assert shared["resolution"]["status"] == "variants"


def test_a_name_that_is_whole_words_of_one_published_name_matches_it() -> None:
    # Students say "Common Grounds" for "We Proudly Serve Starbucks at Common Grounds", and
    # "is that place still open in 45 minutes?" found no hours for it (09-28).
    data = repository()
    venue = copy.deepcopy(IDENTITY)
    venue.update(name="We Proudly Serve Starbucks at Common Grounds", aliases=["Starbucks"])
    other = copy.deepcopy(IDENTITY)
    other.update(id="a8306d1b-0319-477a-88fa-c32e2bab5f4e", name="Common Room Services",
                 aliases=[], links=[{"collection": "contacts", "source_key": "directory",
                                     "source_record_keys": ["other"]}])
    data._artifacts["campus-identities"]["entities"] = [venue, other]
    attach_rows(data, *rows())
    for spelled in ("Common Grounds", "common  grounds", "Starbucks at Common Grounds"):
        result = data.lookup_profile(ProfileQuery(entity=spelled, include=["contact"]))
        assert result["resolution"]["status"] == "matched", spelled
        assert result["resolution"]["read_as"] == venue["name"]
    # One word, a phrase two names share, or part of a word names nothing on its own.
    for spelled in ("Grounds", "Common", "Common Ground", "Serve Starbucks at Common Room"):
        result = data.lookup_profile(ProfileQuery(entity=spelled, include=["contact"]))
        assert result["resolution"]["status"] == "no_match", spelled


def test_a_name_matches_without_its_apostrophe() -> None:
    # "When does dunkin close" found nothing: the venue is published as "Dunkin'".
    data = repository()
    data._artifacts["campus-identities"]["entities"][0].update(name="Example's Center", aliases=[])
    attach_rows(data, *rows())
    for spelled in ("Examples Center", "example’s center", "Example's Center"):
        result = data.lookup_profile(ProfileQuery(entity=spelled, include=["contact"]))
        assert result["resolution"]["status"] == "matched", spelled


def test_a_name_matches_without_its_leading_article() -> None:
    # "Atrium hours tomorrow" found nothing: the venue is published as "The Atrium".
    data = repository()
    data._artifacts["campus-identities"]["entities"][0].update(name="The Example Atrium",
                                                               aliases=[])
    attach_rows(data, *rows())
    for spelled in ("Example Atrium", "the example atrium", "The Example Atrium"):
        result = data.lookup_profile(ProfileQuery(entity=spelled, include=["contact"]))
        assert result["resolution"]["status"] == "matched", spelled
    # An article inside a name stays: "Office of the President" is not "President".
    data._artifacts["campus-identities"]["entities"][0].update(name="Office of the Example")
    result = data.lookup_profile(ProfileQuery(entity="Example", include=["contact"]))
    assert result["resolution"]["status"] != "matched"


def test_a_shared_alias_over_qualified_lines_returns_each_line() -> None:
    # "Campus Police" is an alias of both Public Safety lines, not a prefix of their names.
    data = repository()
    emergency = copy.deepcopy(IDENTITY)
    emergency.update(name="Example Center (Emergency)", aliases=["Front Desk"])
    other = copy.deepcopy(IDENTITY)
    other.update(id="a8306d1b-0319-477a-88fa-c32e2bab5f4e", name="Example Center (Non-Emergency)",
                 aliases=["Front Desk"], links=[
                     {"collection": "contacts", "source_key": "directory",
                      "source_record_keys": ["other"]}])
    data._artifacts["campus-identities"]["entities"] = [emergency, other]
    data._artifacts["campus-identity-coverage"] = {"alias_sources": [
        {"entity_id": line["id"], "alias": "Front Desk",
         "sources": [{"basis": "human_reviewed", "reviewed_at": "2026-09-28"}]}
        for line in (emergency, other)]}
    contact, _ = rows()
    second = {**contact, "id": "contact-2", "source_record_key": "other"}
    data._fetch = Mock(  # type: ignore[method-assign]
        side_effect=lambda _sql, params: [second] if params[2] == ["other"] else [contact]
    )
    result = data.lookup_profile(ProfileQuery(entity="front desk", include=["contact"]))
    assert result["resolution"]["status"] == "variants"
    # The shared alias is a name of each line, so the checker can accept the wording.
    assert "Front Desk" in {name["name"] for name in result["resolution"]["entity_names"]}
    # Different base names under one alias stay ambiguous.
    other["name"] = "Another Center (Non-Emergency)"
    data._artifacts["campus-identities"]["entities"] = [emergency, other]
    assert data.lookup_profile(ProfileQuery(entity="front desk", include=["contact"]))[
        "resolution"]["status"] == "ambiguous"


def test_a_leading_campus_name_is_dropped_only_when_the_name_as_given_matches_nothing() -> None:
    data = repository()
    attach_rows(data, *rows())

    def resolve(name: str) -> dict[str, Any]:
        output = data.lookup_profile(ProfileQuery(entity=name, include=["contact"]))
        resolution: dict[str, Any] = output["resolution"]
        return resolution

    for name in ("Ramapo Example Center", "the Ramapo College’s EC", "RCNJ ec",
                 "Ramapo College of New Jersey Example Center", "The Example Center"):
        resolution = resolve(name)
        assert (resolution["status"], resolution["entity"]["id"]) == ("matched", ENTITY_ID), name
        assert resolution["read_as"] in {"Example Center", "EC", "ec"}
    assert "read_as" not in resolve("Example Center")
    # The rest must still be an exact name or alias, and a bare prefix is no name at all.
    assert resolve("Ramapo Example")["status"] == "no_match"
    assert "read_as" not in resolve("Ramapo")
    # A published name that starts with the campus name is matched as given.
    named = copy.deepcopy(IDENTITY)
    named.update(id="a8306d1b-0319-477a-88fa-c32e2bab5f4e", name="Ramapo Example Center",
                 aliases=[], links=[{"collection": "contacts", "source_key": "directory",
                                     "source_record_keys": ["other"]}])
    data._artifacts["campus-identities"]["entities"].append(named)
    resolution = resolve("ramapo example center")
    assert (resolution["entity"]["id"], "read_as" in resolution) == (named["id"], False)


@pytest.mark.parametrize("hours_result", [[], RuntimeError("internal database failure")])
def test_missing_or_failed_hours_preserve_available_contact(hours_result: Any) -> None:
    data = repository()
    contact, _ = rows()
    data._fetch = Mock(side_effect=[[contact], hours_result])  # type: ignore[method-assign]
    result = data.lookup_profile(ProfileQuery(entity="EC", include=["contact", "hours"]))
    assert result["status"] == "ok"
    assert result["records"][0]["fields"]["phone"] == "201-555-0100"
    assert result["components"]["contact"]["status"] == "available"
    assert result["components"]["hours"]["status"] in {"missing", "unavailable"}
    assert "internal database failure" not in json.dumps(result)


def test_conflicting_phone_does_not_erase_agreed_email() -> None:
    data = repository()
    contact, _ = rows()
    other = {**contact, "id": "other-contact", "phone": "201-555-9999", "total": 2}
    contact["total"] = 2
    data._fetch = Mock(return_value=[contact, other])  # type: ignore[method-assign]
    result = data.lookup_profile(ProfileQuery(entity="EC", include=["contact"]))
    component = result["components"]["contact"]
    assert component["fields"]["phone"] == "conflict"
    assert component["fields"]["email"] == "published"
    assert len(component["conflicts"]["phone"]) == 2
    assert len(result["records"]) == 2
    assert all(
        any("disagree on phone" in note for note in r["limitations"]) for r in result["records"]
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"schema_version": 2, "entities": []},
        {"schema_version": True, "entities": []},
    ],
)
def test_old_or_invalid_identity_artifact_fails_without_guessing(payload: Any) -> None:
    data = repository()
    data._artifacts["campus-identities"] = payload
    result = data.lookup_profile(ProfileQuery(entity="EC", include=["contact"]))
    assert result["status"] == "unavailable"
    assert result["records"] == []


@pytest.mark.parametrize(
    "selector", [{}, {"entity": "EC", "entity_id": ENTITY_ID}, {"entity": " "}]
)
def test_selector_is_explicit(selector: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProfileQuery(**selector, include=["contact"])


def test_partial_delivery_withholds_profile_summaries() -> None:
    data = repository()
    attach_rows(data, *rows())
    result = data.lookup_profile(ProfileQuery(entity="EC", include=["contact", "hours"]))
    delivered = bounded_result(result, lambda output: len(output.get("records", [])) <= 1)
    assert delivered["truncated"]
    assert "components" not in delivered
    assert delivered["components_withheld"] == "retrieval_delivery_limit"


def test_tool_runs_through_generated_answer_review() -> None:
    data = repository()
    attach_rows(data, *rows())
    client = Mock()
    client.create.side_effect = [
        tools(
            SimpleNamespace(
                type="function_call",
                name="lookup_profile",
                call_id="profile",
                arguments=json.dumps({"entity": "EC", "include": ["contact", "hours"]}),
            )
        ),
        answer("EC's published phone number is 201-555-0100.", "campus_fact", ["contacts:contact"]),
        review("supported"),
    ]
    result = run_turn(
        [ChatMessage(role="user", content="What are EC's hours and phone number?")],
        client=client,
        data=data,
        model="test",
        now=NOW,
    )
    assert result["metrics"]["reviewCalls"] == 1
    assert result["trace"][0]["tool"] == "lookup_profile"
    tool = next(tool for tool in tool_definitions() if tool["name"] == "lookup_profile")
    assert set(tool["parameters"]["required"]) == {
        "entity", "entity_id", "include", "date", "meal", "menu_limit", "relationship", "direction",
        "cohort", "plan", "diet",
    }


def test_explicit_date_with_no_hours_for_that_day_is_missing_not_closed() -> None:
    data = repository()
    attach_rows(data, *rows())
    result = data.lookup_profile(
        ProfileQuery(entity="EC", include=["hours"], date=date(2026, 9, 22))
    )
    assert result["components"]["hours"]["status"] == "missing"
    assert result["records"] == []


@pytest.mark.parametrize(
    "bounds",
    [
        {"valid_until": "2026-09-20"},
        {"valid_from": "2026-09-22"},
    ],
)
def test_contact_validity_is_respected_without_erasing_applicable_hours(
    bounds: dict[str, str],
) -> None:
    data = repository()
    contact, hours = rows()
    contact.update(bounds)
    attach_rows(data, contact, hours)
    output = data.lookup_profile(ProfileQuery(entity="EC", include=["contact", "hours"]))
    assert output["components"]["contact"]["status"] == "missing"
    assert output["components"]["hours"]["status"] == "available"
    assert [record["collection"] for record in output["records"]] == ["campus_hours"]


def test_contact_preferences_conflict_on_records_used_by_reviewer() -> None:
    data = repository()
    first, _ = rows()
    first.update(preferred_contact="email", contact_note="Email preferred", total=2)
    second = {**first, "id": "contact-other", "preferred_contact": "phone",
              "contact_note": "Phone preferred"}
    data._fetch = Mock(return_value=[first, second])  # type: ignore[method-assign]
    output = data.lookup_profile(ProfileQuery(entity="EC", include=["contact"]))
    assert output["components"]["contact"]["fields"]["preferred_contact"] == "conflict"
    for record in output["records"]:
        assert record["coverage"]["fields"]["preferred_contact"] == "conflict"
        assert record["coverage"]["fields"]["email"] == "published"
