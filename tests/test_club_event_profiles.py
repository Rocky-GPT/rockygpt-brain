"""Club identities and dated event instances retain source and relationship boundaries."""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rockygpt_brain.api.app import app
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.retrieval.models import SearchQuery
from rockygpt_brain.retrieval.profiles import IdentityRegistry, ProfileQuery
from test_engine import answer, review, tools
from test_profiles import NOW, repository

CLUB = "a5016262-809d-4a28-9671-a0c62b8ba856"
EVENT = "723d9724-940b-483b-af26-88b52928834f"
OTHER_EVENT = "75b4ff66-d918-4c15-b230-f0db137026a2"
CLUB_URL = "https://archway.example.edu/ExampleClub/"
EVENT_URL = "https://archway.example.edu/rsvp_boot?id=100"


def campus() -> Any:
    data = repository()
    for source in ("archway-clubs", "archway-events"):
        data.sources[source] = {
            **data.sources["directory"], "id": source, "source_key": source,
            "canonical_url": f"https://archway.example.edu/{source}",
        }
    club = {
        "id": CLUB, "kind": "club", "name": "Example Club", "aliases": ["EC Club"],
        "links": [{"collection": "clubs", "source_key": "archway-clubs",
                   "source_record_keys": ["Example Club"]}],
    }
    event = {
        "id": EVENT, "kind": "event", "name": "Welcome Meeting — 2026-09-22",
        "aliases": ["Welcome Meeting"],
        "links": [{"collection": "events", "source_key": "archway-events",
                   "source_record_keys": ["Sep22:Welcome Meeting"],
                   "source_record_ids": ["event-one"]}],
    }
    data._artifacts["campus-identities"]["entities"] = [club, event]
    data._artifacts["campus-identity-coverage"] = None
    data._artifacts["clubs"] = [{
        "name": "Example Club", "websiteUrl": CLUB_URL, "email": "club@example.edu",
        "bucket": "student_orgs", "instagramUrl": "https://instagram.example/club",
    }]
    data._artifacts["events"] = [{
        "url": EVENT_URL, "location": "Private Location (register to display)",
        "tags": ["Meeting"], "ticketStatus": "FREE",
    }]
    data._artifacts["event-organizers"] = None
    common = {"collected_at": NOW, "valid_from": None, "valid_until": None, "total": 1}
    rows = [
        {**common, "id": "club-one", "source_id": "archway-clubs",
         "source_record_key": "Example Club", "name": "Example Club",
         "category": "Student Organization", "website_url": CLUB_URL},
        {**common, "id": "event-one", "source_id": "archway-events",
         "source_record_key": "Sep22:Welcome Meeting", "title": "Welcome Meeting",
         "date_label": "Tue, Sep 22, 2026", "starts_at": "2026-09-22T17:00:00-04:00",
         "start_time": "5 PM", "end_time": "6 PM", "organizer": "Example Club",
         "event_url": EVENT_URL, "description": "The published meeting description."},
    ]
    # Tests adjust these rows in place through data.test_rows.
    data.test_rows = rows  # type: ignore[attr-defined]

    def fetch(_sql: Any, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        if len(params) < 3:
            return [row for row in rows if row["source_id"] == "archway-clubs"]
        return [row for row in rows if row["source_id"] == params[1]
                and row["source_record_key"] in params[2]
                and (len(params) < 4 or row["id"] in params[3])]

    data._fetch = Mock(side_effect=fetch)  # type: ignore[method-assign]
    return data


def add_other_occurrence(data: Any, *, same_key: bool = False) -> None:
    entity = copy.deepcopy(data._artifacts["campus-identities"]["entities"][1])
    entity.update(id=OTHER_EVENT, name="Welcome Meeting — 2026-09-23")
    key = "Sep22:Welcome Meeting" if same_key else "Sep23:Welcome Meeting"
    entity["links"][0].update(source_record_keys=[key], source_record_ids=["event-two"])
    data._artifacts["campus-identities"]["entities"].append(entity)
    data.test_rows.append({
        **data.test_rows[1], "id": "event-two", "source_record_key": key,
        "starts_at": "2026-09-23T17:00:00-04:00", "date_label": "Wed, Sep 23, 2026",
        "event_url": "https://archway.example.edu/rsvp_boot?id=101",
    })


def add_organizer(data: Any) -> None:
    data._artifacts["campus-identities"]["entities"][1]["relationships"] = [{
        "type": "organized_by", "target_entity_id": CLUB,
        "evidence": [{"collection": "events", "source_key": "archway-events",
                      "source_record_key": "Sep22:Welcome Meeting", "source_record_id": "event-one",
                      "field": "organizer_group_id", "source_url": EVENT_URL}],
    }]
    data._artifacts["event-organizers"] = {"schema_version": 1, "events": [{
        "source_key": "archway-events", "source_record_key": "Sep22:Welcome Meeting",
        "source_record_id": "event-one", "event_url": EVENT_URL,
        "organizer_group_id": "g100", "organizer_url": CLUB_URL, "organizer_name": "Example Club",
        "collected_at": "2026-09-20T15:00:00Z", "source_url": EVENT_URL,
    }]}


def test_club_contact_is_selective_and_does_not_invent_meetings_or_officer_identity() -> None:
    data = campus()
    result = data.lookup_profile(ProfileQuery(entity="EC Club", include=["club", "contact"]))
    record = result["records"][0]
    assert record["id"] == "clubs:club-one" and record["canonical_entity_id"] == CLUB
    assert record["url"] == CLUB_URL and record["collected_at"] == NOW.isoformat()
    assert record["fields"]["email"] == "club@example.edu"
    assert record["fields"]["category"] == "Student Organization"
    assert result["components"]["contact"]["fields"]["phone"] == "not_published"
    assert "does not establish current meetings" in result["components"]["club"]["limitations"][0]
    contact = data.lookup_profile(ProfileQuery(entity_id=UUID(CLUB), include=["contact"]))
    assert "category" not in contact["records"][0]["fields"]
    assert "identity_relationships" not in contact["records"][0]["fields"]


@pytest.mark.parametrize("failure", ["no_url_match", "duplicate_url", "missing_email"])
def test_club_artifact_uncertainty_never_guesses_email(failure: str) -> None:
    data = campus()
    raw = data._artifacts["clubs"][0]
    if failure == "no_url_match":
        raw["websiteUrl"] = "https://example.edu/another-club"
    elif failure == "duplicate_url":
        data._artifacts["clubs"].append({**raw, "email": "other@example.edu"})
    else:
        raw.pop("email")
    output = data.lookup_profile(ProfileQuery(entity="Example Club", include=["contact"]))
    assert output["components"]["contact"]["fields"]["email"] == "not_published"
    assert "email" not in output["records"][0]["fields"]
    assert output["records"][0]["fields"]["website_url"] == CLUB_URL


def test_event_keeps_future_occurrence_and_unknown_location_without_identity_guess() -> None:
    data = campus()
    output = data.lookup_profile(ProfileQuery(entity="Welcome Meeting", include=["event"]))
    event = output["records"][0]
    assert event["id"] == "events:event-one" and event["url"] == EVENT_URL
    assert event["fields"]["occurrence_date"] == "2026-09-22"
    assert event["fields"]["timezone"] == "America/New_York"
    assert event["fields"]["location"] is None
    assert event["fields"]["location_access"] == "registration_required"
    component = output["components"]["event"]
    assert component["temporal_scope"] == "dated_event_occurrence"
    assert component["requested_date"] is None
    assert component["occurrence_dates"] == ["2026-09-22"]
    assert component["relationships"] == []
    assert component["fields"]["organizer"] == "published"


def test_event_occurrence_dates_follow_campus_timezone_and_missing_clock_stays_unknown() -> None:
    data = campus()
    data.test_rows[1].update(starts_at="2026-09-22T02:00:00+00:00", start_time="10 PM")
    match = data.lookup_profile(ProfileQuery(
        entity_id=UUID(EVENT), include=["event"], date=date(2026, 9, 21),
    ))
    assert match["components"]["event"]["occurrence_dates"] == ["2026-09-21"]
    assert data.lookup_profile(ProfileQuery(
        entity_id=UUID(EVENT), include=["event"], date=date(2026, 9, 22),
    ))["records"] == []
    data.test_rows[1].update(starts_at="2026-09-22T00:00:00-04:00", start_time=None, end_time=None)
    output = data.lookup_profile(ProfileQuery(entity_id=UUID(EVENT), include=["event"]))
    record = output["records"][0]
    assert record["coverage"]["fields"]["starts_at"] == "date_only"
    assert output["components"]["event"]["fields"]["start_time"] == "not_published"
    assert "start time is unavailable" in record["limitations"][-1]


def test_missing_event_date_is_available_by_identity_but_never_matches_an_invented_date() -> None:
    data = campus()
    data.test_rows[1].update(starts_at=None, date_label=None, start_time=None)
    output = data.lookup_profile(ProfileQuery(entity_id=UUID(EVENT), include=["event"]))
    assert output["records"][0]["fields"]["occurrence_date"] is None
    assert output["components"]["event"]["fields"]["date_label"] == "not_published"
    assert output["components"]["event"]["occurrence_dates"] == []
    filtered = data.lookup_profile(ProfileQuery(
        entity_id=UUID(EVENT), include=["event"], date=date(2026, 9, 22),
    ))
    assert filtered["records"] == []


def test_repeated_title_requires_identity_or_exact_campus_date_not_latest_name_guess() -> None:
    data = campus()
    add_other_occurrence(data)
    ambiguous = data.lookup_profile(ProfileQuery(entity="Welcome Meeting", include=["event"]))
    assert ambiguous["resolution"]["status"] == "ambiguous" and ambiguous["records"] == []
    data._fetch.assert_not_called()
    chosen = data.lookup_profile(ProfileQuery(
        entity="Welcome Meeting", include=["event"], date=date(2026, 9, 23),
    ))
    assert chosen["resolution"]["entity"]["id"] == OTHER_EVENT
    assert chosen["records"][0]["id"] == "events:event-two"
    data.test_rows[1]["starts_at"] = None
    uncertain = data.lookup_profile(ProfileQuery(
        entity="Welcome Meeting", include=["event"], date=date(2026, 9, 23),
    ))
    assert uncertain["resolution"]["status"] == "ambiguous"


def test_large_repeated_title_set_stays_ambiguous_without_unbounded_queries() -> None:
    data = campus()
    original = data._artifacts["campus-identities"]["entities"][1]
    for index in range(20):
        entity = copy.deepcopy(original)
        entity.update(id=str(UUID(int=index + 1)), name=f"Welcome Meeting instance {index}")
        entity["links"][0].update(source_record_keys=[f"key-{index}"],
                                 source_record_ids=[f"row-{index}"])
        data._artifacts["campus-identities"]["entities"].append(entity)
    result = data.lookup_profile(ProfileQuery(
        entity="Welcome Meeting", include=["event"], date=date(2026, 9, 22),
    ))
    assert result["resolution"]["status"] == "ambiguous"
    assert result["resolution"]["total_candidates"] == 21
    assert result["resolution"]["candidates_truncated"]
    data._fetch.assert_not_called()


def test_colliding_event_keys_require_distinct_original_row_ids_and_broken_id_is_missing() -> None:
    data = campus()
    add_other_occurrence(data, same_key=True)
    registry = data._artifacts["campus-identities"]
    IdentityRegistry.model_validate(registry)
    for identity, record_id in [(EVENT, "event-one"), (OTHER_EVENT, "event-two")]:
        output = data.lookup_profile(ProfileQuery(entity_id=UUID(identity), include=["event"]))
        assert [record["id"] for record in output["records"]] == [f"events:{record_id}"]
    registry["entities"][1]["links"][0]["source_record_ids"] = ["missing-row"]
    missing = data.lookup_profile(ProfileQuery(entity_id=UUID(EVENT), include=["event"]))
    assert missing["records"] == []
    assert missing["components"]["event"]["linked_records_missing"] > 0
    registry["entities"][1]["links"][0]["source_record_ids"] = ["event-two"]
    with pytest.raises(ValidationError):
        IdentityRegistry.model_validate(registry)
    registry["entities"].pop()
    registry["entities"][1]["links"][0].pop("source_record_ids")
    ambiguous = data.lookup_profile(ProfileQuery(entity_id=UUID(EVENT), include=["event"]))
    assert ambiguous["records"] == []
    assert ambiguous["components"]["event"]["linked_records_missing"] == 1


def test_explicit_organizer_has_separate_source_clock_and_survives_date_filter() -> None:
    data = campus()
    add_organizer(data)
    output = data.lookup_profile(ProfileQuery(
        entity_id=UUID(EVENT), include=["event"], date=date(2026, 9, 22),
    ))
    relationship = output["components"]["event"]["relationships"][0]
    assert relationship["target"]["id"] == CLUB
    assert relationship["evidence_ids"] == ["events:event-one:organizer"]
    assert output["records"][0]["collected_at"] == NOW.isoformat()
    assert output["records"][1]["collected_at"] == "2026-09-20T15:00:00+00:00"
    assert output["records"][1]["fields"]["organizer_group_id"] == "g100"


def test_club_reverse_events_require_proof_and_preserve_occurrence_identity() -> None:
    data = campus()
    add_organizer(data)
    output = data.lookup_profile(ProfileQuery(
        entity_id=UUID(CLUB), include=["event", "contact"], date=date(2026, 9, 22),
    ))
    event = next(record for record in output["records"] if record["id"] == "events:event-one")
    assert event["canonical_entity_id"] == EVENT
    assert event["related_to_entity_id"] == CLUB
    assert event["relationship_to_entity"] == "organized_by"
    component = output["components"]["event"]
    assert component["temporal_scope"] == "linked_event_occurrences"
    assert component["linked_event_candidates"] == 1
    assert component["unexamined_event_candidates"] == 0
    assert component["relationships"][0]["source"]["id"] == EVENT
    assert component["fields"] == {}  # Distinct occurrences are not conflicting schedule values.
    assert output["components"]["contact"]["fields"]["email"] == "published"
    assert data.lookup_profile(ProfileQuery(
        entity_id=UUID(CLUB), include=["event"], date=date(2026, 9, 23),
    ))["records"] == []
    data._artifacts["event-organizers"] = None
    broken = data.lookup_profile(ProfileQuery(entity_id=UUID(CLUB), include=["event", "contact"]))
    assert broken["components"]["event"]["relationships_missing"] == 1
    assert broken["components"]["event"]["evidence_ids"] == []
    assert broken["components"]["contact"]["status"] == "available"


def test_reverse_event_candidate_bound_is_explicit_not_an_exhaustive_no_events_claim() -> None:
    data = campus()
    add_organizer(data)
    original = data._artifacts["campus-identities"]["entities"][1]
    for index in range(21):
        entity = copy.deepcopy(original)
        entity.update(id=str(UUID(int=index + 1)), name=f"Other instance {index}", aliases=[])
        entity["links"][0].update(source_record_keys=[f"missing-key-{index}"],
                                 source_record_ids=[f"missing-row-{index}"])
        data._artifacts["campus-identities"]["entities"].append(entity)
    output = data.lookup_profile(ProfileQuery(entity_id=UUID(CLUB), include=["event"]))
    component = output["components"]["event"]
    assert component["linked_event_candidates"] == 22
    assert component["unexamined_event_candidates"] == 2
    assert component["truncated"] and component["reason"] == "event_candidate_limit"
    assert "absence does not mean" in component["limitations"][0]
    assert data._fetch.call_count == 21  # One start-time read, then one per examined candidate.


@pytest.mark.parametrize("conflict", [False, True])
def test_repeated_page_captures_keep_each_timestamp_and_do_not_resolve_conflicts(
    conflict: bool,
) -> None:
    data = campus()
    add_organizer(data)
    original = data._artifacts["event-organizers"]["events"][0]
    later = {**original, "collected_at": "2026-09-21T15:00:00Z"}
    if conflict:
        later["organizer_group_id"] = "other-group"
    data._artifacts["event-organizers"]["events"].extend([later, dict(original)])
    output = data.lookup_profile(ProfileQuery(entity_id=UUID(EVENT), include=["event"]))
    captures = [record for record in output["records"] if "event_record_id" in record["fields"]]
    assert len(captures) == 2 and len({record["id"] for record in captures}) == 2
    assert {record["collected_at"] for record in captures} == {
        "2026-09-20T15:00:00+00:00", "2026-09-21T15:00:00+00:00",
    }
    relationships = output["components"]["event"]["relationships"]
    if conflict:
        assert relationships == []
        assert output["components"]["event"]["fields"]["organizer_group_id"] == "conflict"
    else:
        assert set(relationships[0]["evidence_ids"]) == {record["id"] for record in captures}
    data._artifacts["event-organizers"]["events"].reverse()
    repeated = data.lookup_profile(ProfileQuery(entity_id=UUID(EVENT), include=["event"]))
    assert {record["id"] for record in repeated["records"]} == {
        record["id"] for record in output["records"]
    }


def test_organizer_name_alone_cannot_be_approved_as_an_identity_relationship() -> None:
    data = campus()
    add_organizer(data)
    relationship = data._artifacts["campus-identities"]["entities"][1]["relationships"][0]
    relationship["evidence"][0]["field"] = "organizer"
    with pytest.raises(ValidationError):
        IdentityRegistry.model_validate(data._artifacts["campus-identities"])


@pytest.mark.parametrize("failure", ["missing_page", "wrong_original_id", "wrong_target_kind"])
def test_unsupported_organizer_link_preserves_event(failure: str) -> None:
    data = campus()
    add_organizer(data)
    if failure == "missing_page":
        data._artifacts["event-organizers"] = None
    elif failure == "wrong_original_id":
        data._artifacts["event-organizers"]["events"][0]["source_record_id"] = "other-row"
    else:
        data._artifacts["campus-identities"]["entities"][0]["kind"] = "office"
    output = data.lookup_profile(ProfileQuery(entity_id=UUID(EVENT), include=["event"]))
    assert output["records"][0]["id"] == "events:event-one"
    assert output["components"]["event"]["relationships"] == []
    assert output["components"]["event"]["relationships_missing"] == 1


def test_event_rename_reschedule_and_row_refresh_preserve_persistent_identity() -> None:
    data = campus()
    data.test_rows[1].update(
        id="new-row", title="Renamed meeting", starts_at="2026-10-01T17:00:00-04:00",
    )
    entity = data._artifacts["campus-identities"]["entities"][1]
    entity.update(name="Renamed meeting — 2026-10-01", aliases=["Welcome Meeting"])
    entity["links"][0]["source_record_ids"] = ["new-row"]
    output = data.lookup_profile(ProfileQuery(entity_id=UUID(EVENT), include=["event"]))
    assert output["records"][0]["id"] == "events:new-row"
    assert output["resolution"]["entity"]["id"] == EVENT
    assert output["components"]["event"]["occurrence_dates"] == ["2026-10-01"]


def test_unlinked_club_remains_available_to_broad_search_with_original_contact_evidence() -> None:
    data = campus()
    data._artifacts["campus-identities"]["entities"] = []
    assert data.lookup_profile(ProfileQuery(
        entity="Example Club", include=["club"],
    ))["resolution"]["status"] == "no_match"
    output = data.search(SearchQuery(collection="clubs", query="Example"))
    assert output["records"][0]["id"] == "clubs:club-one"
    assert output["records"][0]["fields"]["email"] == "club@example.edu"


def test_event_to_club_followup_uses_normal_tools_and_review_without_model_bypass() -> None:
    data = campus()
    add_organizer(data)
    client = Mock()
    calls = [tools(SimpleNamespace(
        type="function_call", name="lookup_profile", call_id=f"profile{index}",
        arguments=json.dumps(arguments),
    )) for index, arguments in enumerate([
        {"entity_id": EVENT, "include": ["event"]},
        {"entity_id": CLUB, "include": ["contact"]},
    ])]
    client.create.side_effect = [*calls, answer(
        "The organizer is Example Club; its email is club@example.edu.", "campus_fact",
        ["events:event-one:organizer", "clubs:club-one"],
    ), review("supported")]
    output = run_turn([
        ChatMessage(role="user", content="Tell me about Welcome Meeting on September 22."),
        ChatMessage(role="assistant", content="Welcome Meeting is organized by Example Club."),
        ChatMessage(role="user", content="How do I contact the organizer?"),
    ], client=client, data=data, model="test", now=NOW)
    assert output["status"] == "answered"
    assert output["metrics"]["reviewCalls"] == 1
    assert output["trace"][0]["components"]["event"]["relationships"][0]["target"]["id"] == CLUB
    assert output["trace"][1]["resolution"]["entity"]["id"] == CLUB


def test_dev_api_serves_new_sections_without_default_today_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured")
    data = campus()
    with patch("rockygpt_brain.api.identities.CampusData", return_value=data):
        index = TestClient(app).get("/v1/dev/identities")
        response = TestClient(app).get(f"/v1/dev/identities/{EVENT}", params={"include": "event"})
    assert {entity["kind"] for entity in index.json()["identities"]} == {"club", "event"}
    assert response.status_code == 200
    component = response.json()["profile"]["components"]["event"]
    assert component["requested_date"] is None
    assert component["occurrence_dates"] == ["2026-09-22"]


def organization(data: Any) -> Any:
    data._artifacts["campus-identities"]["entities"][0]["kind"] = "organization"
    add_organizer(data)
    return data


def test_organizations_use_the_same_evidenced_organizer_links_as_clubs() -> None:
    data = organization(campus())
    group = data.lookup_profile(ProfileQuery(entity_id=UUID(CLUB), include=["event"]))
    component = group["components"]["event"]
    assert component["temporal_scope"] == "linked_event_occurrences"
    assert [r["source"]["id"] for r in component["relationships"]] == [EVENT]
    assert "absence does not mean" in component["limitations"][0]
    occurrence = data.lookup_profile(ProfileQuery(entity_id=UUID(EVENT), include=["event"]))
    target = occurrence["components"]["event"]["relationships"][0]["target"]
    assert target == {"id": CLUB, "name": "Example Club", "kind": "organization"}
    incoming = data.lookup_profile(ProfileQuery(
        entity_id=UUID(CLUB), include=["related"], direction="incoming"))
    assert [(r["type"], r["entity"]["id"]) for r in
            incoming["components"]["related"]["relationships"]] == [("organized_by", EVENT)]


def test_group_events_are_examined_soonest_first_and_narrowed_by_date() -> None:
    data = organization(campus())
    entities = data._artifacts["campus-identities"]["entities"]
    starts: dict[str, Any] = {}
    offsets: dict[str, int] = {}
    for index in range(25):
        entity = copy.deepcopy(entities[1])
        entity.update(id=str(UUID(int=index + 1)), name=f"Occurrence {index}", aliases=[])
        entity["links"][0].update(source_record_keys=[f"key-{index}"],
                                 source_record_ids=[f"row-{index}"])
        entities.append(entity)
        offset = (index * 7) % 25 - 5  # -5..19 days, deliberately not in ID order.
        starts[f"row-{index}"] = NOW + timedelta(days=offset)
        offsets[f"key-{index}"] = offset
    inner = data._fetch.side_effect

    def fetch(sql_text: Any, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        if "starts_at" in str(sql_text):
            return [{"id": row, "starts_at": starts[row]} for row in params[1] if row in starts]
        return list(inner(sql_text, params))

    data._fetch = Mock(side_effect=fetch)

    def examined() -> list[str]:
        return [key for call in data._fetch.call_args_list if len(call.args[1]) >= 3
                for key in call.args[1][2]]

    component = data.lookup_profile(
        ProfileQuery(entity_id=UUID(CLUB), include=["event"]))["components"]["event"]
    assert component["linked_event_candidates"] == 26
    assert component["unexamined_event_candidates"] == 6
    assert [offsets[key] for key in examined()] == list(range(20))  # Upcoming, soonest first.
    data._fetch.reset_mock()
    day = (NOW + timedelta(days=3)).date()
    data.lookup_profile(ProfileQuery(entity_id=UUID(CLUB), include=["event"], date=day))
    # The unknown-date occurrence cannot be excluded by a date it may match.
    assert sorted(examined()) == sorted([
        next(key for key, offset in offsets.items() if offset == 3), "Sep22:Welcome Meeting",
    ])


def test_club_narrative_artifact_fields_reach_canonical_profile_with_locator() -> None:
    data = campus()
    data._artifacts["clubs"][0].update(
        mission="Support sustainable practices.", memberBenefits="Service projects.",
        membershipInfo="Lifetime membership")
    result = data.lookup_profile(ProfileQuery(entity_id=UUID(CLUB), include=["club"]))
    properties = {item["key"]: item for item in result["entity_facts"]["properties"]}
    assert properties["email"]["values"][0]["value"] == "club@example.edu"
    assert properties["mission"]["values"][0]["value"] == "Support sustainable practices."
    assert properties["member_benefits"]["values"][0]["value"] == "Service projects."
    assert properties["membership_info"]["values"][0]["value"] == "Lifetime membership"
    source = result["entity_facts"]["sources"][0]
    assert source["artifact_key"] == "clubs" and source["artifact_path"] == ["0"]
    assert result["components"]["club"]["fields"]["mission"] == "published"
