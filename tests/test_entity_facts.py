"""Canonical office evidence contract, independent of any paid model."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from rockygpt_brain.retrieval import (
    DatasetChanged,
    EvidenceUnavailable,
    InvalidFactRequest,
    MemoryEntityFacts,
    PostgresEntityFacts,
    UnknownEntity,
)
from rockygpt_brain.retrieval.entity_facts import canonical_properties

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def office(
    entity_id: str = "registrar",
    name: str = "Registrar",
    keys: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": entity_id,
        "kind": "office",
        "name": name,
        "aliases": [],
        "links": [
            {
                "collection": "contacts",
                "source_key": "campus-directory",
                "source_record_keys": keys or [f"office:{entity_id}"],
            }
        ],
    }


def contact(record_id: str = "r1", **changes: Any) -> dict[str, Any]:
    return {
        "id": record_id,
        "source_key": "campus-directory",
        "source_record_key": "office:registrar",
        "name": "Registrar",
        "phone": "(201) 555-0100",
        "email": "Registrar@example.edu",
        "office": "D224",
        "collected_at": NOW,
        "freshness_sla_hours": 24,
        "canonical_url": "https://example.edu/directory",
        "content_hash": "record-hash",
        **changes,
    }


def reader(
    contacts: list[dict[str, Any]] | None = None,
    entities: list[dict[str, Any]] | None = None,
    aliases: list[dict[str, Any]] | None = None,
) -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="test-release",
        identity_hash="identity-hash",
        entities=entities if entities is not None else [office()],
        contacts=contacts if contacts is not None else [contact()],
        alias_sources=aliases,
        now=lambda: NOW,
    )


def prop(result: dict[str, Any], key: str) -> dict[str, Any]:
    return next(item for item in result["properties"] if item["key"] == key)


def test_search_keeps_shared_alias_ambiguous_and_pins_release() -> None:
    entities = [office("a", "First Office"), office("b", "Second Office")]
    for entity in entities:
        entity["aliases"] = ["Student Services"]
    result = reader(entities=entities).search_offices(" student   services ")
    assert [candidate["entity_id"] for candidate in result["candidates"]] == ["a", "b"]
    assert all(candidate["match"] == "exact" for candidate in result["candidates"])
    assert result["dataset_version"] == "test-release"
    assert result["identity_hash"] == "identity-hash"
    assert "phone" not in result["candidates"][0]


def test_search_reports_truncation_and_never_fuzzy_merges() -> None:
    service = reader(entities=[office(str(i), f"Student {i}") for i in range(3)])
    result = service.search_offices("Student", limit=2)
    assert len(result["candidates"]) == 2
    assert result["truncated"] is True
    assert service.search_offices("Studant")["candidates"] == []


@pytest.mark.parametrize(
    "published,query",
    [
        ("Registrar", "Registrar office"),
        ("Office of the Registrar", "the Registrar’s office"),
        ("Office of Financial Aid", "Financial-Aid office"),
        ("Office of Financial Aid", "Aid, Financial"),
        ("Health & Counseling", "Counseling and Health office"),
        ("Center for Student Involvement", "Student Involvement Center"),
        ("Women's Center", "the Women’s Center office"),
    ],
)
def test_discovery_matches_name_words_despite_order_punctuation_and_office_filler(
    published: str,
    query: str,
) -> None:
    result = reader(entities=[office("target", published)]).search_offices(query)
    assert [candidate["entity_id"] for candidate in result["candidates"]] == ["target"]
    assert result["candidates"][0]["match"] == "partial"


def test_discovery_keeps_exact_priority_and_all_token_collisions() -> None:
    service = reader(
        entities=[
            office("main", "Financial Aid"),
            office("graduate", "Graduate Financial Aid Office"),
            office("other", "Office of Aid for Financial Services"),
        ]
    )
    exact = service.search_offices("Financial Aid")["candidates"]
    assert exact[0]["entity_id"] == "main"
    assert exact[0]["match"] == "exact"
    assert {candidate["entity_id"] for candidate in exact} == {"main", "graduate", "other"}
    unordered = service.search_offices("Aid Financial office")["candidates"]
    assert {candidate["entity_id"] for candidate in unordered} == {"main", "graduate", "other"}
    assert all(candidate["match"] == "partial" for candidate in unordered)
    limited = service.search_offices("Aid Financial office", limit=1)
    assert limited["truncated"] is True


def test_discovery_does_not_invent_synonyms_or_match_generic_words_alone() -> None:
    service = reader(entities=[office("registrar", "Registrar"), office("aid", "Financial Aid")])
    assert service.search_offices("registration office")["candidates"] == []
    assert service.search_offices("pay tuition")["candidates"] == []
    assert service.search_offices("Graduate scholarships")["candidates"] == []
    assert service.search_offices("the office")["candidates"] == []


@pytest.mark.parametrize(
    "published,query",
    [
        ("Registrar", "Registrar office at Example College"),
        ("Financial Aid", "the Financial Aid office on campus"),
        ("Health & Counseling", "contact Health and Counseling for appointments"),
    ],
)
def test_discovery_accepts_published_names_inside_added_context(
    published: str,
    query: str,
) -> None:
    result = reader(entities=[office("target", published)]).search_offices(query)
    assert [candidate["entity_id"] for candidate in result["candidates"]] == ["target"]
    assert result["candidates"][0]["match"] == "partial"


def test_discovery_retains_every_named_office_in_multi_intent_query() -> None:
    service = reader(entities=[office(), office("admissions", "Admissions")])
    result = service.search_offices("Registrar and Admissions offices at Example College")
    assert {candidate["entity_id"] for candidate in result["candidates"]} == {
        "registrar",
        "admissions",
    }
    assert all(candidate["match"] == "partial" for candidate in result["candidates"])


def test_empty_normalized_name_or_query_tokens_cannot_match_everything() -> None:
    generic = office("generic", "Office")
    generic["aliases"] = ["the office"]
    service = reader(entities=[generic, office()])
    assert service.search_offices("Campus counseling service")["candidates"] == []
    generic_result = service.search_offices("the office")
    assert [candidate["entity_id"] for candidate in generic_result["candidates"]] == ["generic"]
    assert generic_result["candidates"][0]["match"] == "exact"


@pytest.mark.parametrize("version,identity_hash", [("old", None), ("test-release", "old")])
def test_stale_pin_fails_before_returning_facts(version: str, identity_hash: str | None) -> None:
    service = reader()
    with pytest.raises(DatasetChanged):
        service.get_office_facts("registrar", ["email"], version, identity_hash=identity_hash)
    with pytest.raises(DatasetChanged):
        service.search_offices("Registrar", dataset_version=version, identity_hash=identity_hash)


def test_equal_values_preserve_every_record_and_missing_observations() -> None:
    result = reader([contact("r1"), contact("r2"), contact("r3", email=None)]).get_office_facts(
        "registrar",
        ["email"],
        "test-release",
    )
    email = prop(result, "email")
    assert email["status"] == "known"
    assert email["values"] == [
        {
            "value": "Registrar@example.edu",
            "assertion_ids": ["r1:email", "r2:email"],
            "source_ids": ["r1", "r2"],
        }
    ]
    assert len(email["assertions"]) == 3
    assert email["assertions"][2]["raw_value"] is None
    assert result["evidence_count"] == 3  # Records, never votes/confidence.


def test_conflicting_values_are_not_voted_down_or_case_folded() -> None:
    result = reader([contact("r1"), contact("r2"), contact("r3", email="registrar@example.edu")])
    email = prop(result.get_office_facts("registrar", ["email"], "test-release"), "email")
    assert email["status"] == "conflicting"
    assert [value["value"] for value in email["values"]] == [
        "Registrar@example.edu",
        "registrar@example.edu",
    ]


@pytest.mark.parametrize(
    "start,status",
    [("2026-09-02", "multiple"), ("2026-09-01", "conflicting"), (None, "conflicting")],
)
def test_distinct_dates_only_separate_explicit_disjoint_records(
    start: str | None,
    status: str,
) -> None:
    result = reader(
        [
            contact(
                "old", email="old@example.edu", valid_from="2026-01-01", valid_until="2026-09-01"
            ),
            contact("new", email="new@example.edu", valid_from=start, valid_until="2026-12-31"),
        ]
    ).get_office_facts("registrar", ["email"], "test-release")
    assert prop(result, "email")["status"] == status
    assert result["sources"][0]["validity"] == "expired"


def test_source_boundaries_and_verified_page_urls_are_retained() -> None:
    result = reader(
        [
            contact(
                collected_at="2026-09-01T12:00:00Z",
                valid_from="2026-11-01",
                valid_until="2026-11-30",
                normalization_metadata={
                    "evidence": {
                        "source_urls": ["https://example.edu/registrar", "javascript:alert(1)"],
                        "withheld": [
                            {"field": "office", "value": "D224", "reason": "not observed"}
                        ],
                    }
                },
            )
        ]
    ).get_office_facts("registrar", ["email", "website"], "test-release")
    source = result["sources"][0]
    assert source["freshness"] == "stale"
    assert source["validity"] == "future"
    assert source["citation_urls"] == ["https://example.edu/registrar"]
    assert prop(result, "website")["status"] == "unknown"
    assert source["normalization_metadata"]["evidence"]["withheld"]


def test_phone_and_office_aliases_normalize_without_inventing_values() -> None:
    result = reader(
        [
            contact("a", phones=[{"number": "201-555-0100", "extension": "123", "type": "fax"}]),
            contact(
                "b",
                phones=[{"number": "+12015550100", "extension": "123", "type": "fax"}],
                office="D-224",
            ),
        ]
    ).get_office_facts("registrar", ["phone", "office"], "test-release")
    assert prop(result, "phones")["status"] == "known"
    assert prop(result, "phones")["values"][0]["value"] == [
        {"number": "+12015550100", "extension": "123", "type": "fax"},
    ]
    assert prop(result, "offices")["values"][0]["value"] == ["D-224"]
    extension = reader([contact(phone="Ext. 1234")]).get_office_facts(
        "registrar",
        ["phones"],
        "test-release",
    )
    assert prop(extension, "phones")["values"][0]["value"] == [{"extension": "1234"}]
    unparsed = reader([contact(phone="Call the desk")]).get_office_facts(
        "registrar",
        ["phones"],
        "test-release",
    )
    assert prop(unparsed, "phones")["values"][0]["value"] == [{"text": "Call the desk"}]


def test_unknown_is_not_false_and_primitive_types_are_distinct() -> None:
    result = reader([contact(prefers_email=False)]).get_office_facts(
        "registrar",
        ["prefers_email"],
        "test-release",
    )
    preference = prop(result, "prefers_email")
    assert preference["status"] == "unknown"
    assert preference["assertions"][0]["raw_value"] is False
    rows = [contact(str(i), department=value) for i, value in enumerate([False, 0, "0"])]
    properties = canonical_properties(rows, ["department"], [{"id": row["id"]} for row in rows])
    assert properties[0]["status"] == "conflicting"
    assert len(properties[0]["values"]) == 3


def test_reviewed_name_alias_requires_registry_name_in_original_evidence() -> None:
    entity = office(name="Potter Library")
    entity["aliases"] = ["Library"]
    aliases: list[dict[str, Any]] = [
        {"entity_id": "registrar", "alias": "Library", "sources": [{"basis": "department"}]}
    ]
    rows = [contact("a", name="Potter Library"), contact("b", name="Library")]
    resolved = reader(rows, [entity], aliases).get_office_facts(
        "registrar", ["name"], "test-release"
    )
    assert prop(resolved, "name")["status"] == "known"
    assert prop(resolved, "name")["assertions"][1]["raw_value"] == "Library"
    aliases[0]["sources"][0]["basis"] = "record_name"
    unresolved = reader(rows, [entity], aliases).get_office_facts(
        "registrar", ["name"], "test-release"
    )
    assert prop(unresolved, "name")["status"] == "conflicting"
    single = reader([rows[1]], [entity], aliases).get_office_facts(
        "registrar",
        ["name"],
        "test-release",
    )
    assert prop(single, "name")["values"][0]["value"] == "Library"


def test_exact_identity_link_excludes_similar_named_unlinked_records() -> None:
    entity = office()
    entity["links"][0]["source_record_ids"] = ["r1"]
    result = reader(
        [
            contact("r1"),
            contact("r2", email="wrong@example.edu"),
            contact("r3", source_record_key="different-office", email="wrong@example.edu"),
        ],
        [entity],
    ).get_office_facts("registrar", ["email"], "test-release")
    assert result["evidence_count"] == 1
    assert result["sources"][0]["id"] == "r1"


def test_missing_linked_records_are_explicit_and_fabricated_support_rejected() -> None:
    result = reader([], [office(keys=["missing"])]).get_office_facts(
        "registrar",
        ["email"],
        "test-release",
    )
    assert result["complete"] is False
    assert result["caveats"]
    assert prop(result, "email")["status"] == "unknown"
    with pytest.raises(EvidenceUnavailable, match="original evidence"):
        canonical_properties([contact()], ["email"], [])


def test_duplicate_canonical_ownership_fails_closed() -> None:
    with pytest.raises(EvidenceUnavailable, match="multiple canonical owners"):
        reader(entities=[office(), office("other", keys=["office:registrar"])])


def test_contact_budget_never_silently_hides_conflicts() -> None:
    with pytest.raises(EvidenceUnavailable, match="bounded read"):
        reader([contact(str(i)) for i in range(129)]).get_office_facts(
            "registrar",
            ["email"],
            "test-release",
        )


@pytest.mark.parametrize("fields", [[], ["password"], "email", [1]])
def test_bad_fields_are_rejected(fields: Any) -> None:
    with pytest.raises(InvalidFactRequest):
        reader().get_office_facts("registrar", fields, "test-release")


def test_unknown_entity_and_missing_evidence_do_not_report_ready() -> None:
    with pytest.raises(UnknownEntity):
        reader().get_office_facts("not-an-office", ["email"], "test-release")
    assert reader([]).readiness() == {"ready": False}
    assert reader().readiness() == {
        "ready": True,
        "dataset_version": "test-release",
        "identity_hash": "identity-hash",
    }


def test_date_validity_uses_the_campus_calendar_day() -> None:
    service = reader([contact(valid_from="2026-10-01", valid_until="2026-10-01")])
    service.now = lambda: datetime(2026, 10, 2, 1, tzinfo=UTC)  # Still Oct 1 in New Jersey.
    facts = service.get_office_facts("registrar", ["email"], "test-release")
    assert facts["sources"][0]["validity"] == "current"


def test_facts_accept_all_fields_and_one_frozen_turn_clock() -> None:
    service = reader()
    service.now = lambda: datetime(2027, 1, 1, tzinfo=UTC)
    facts = service.get_office_facts("registrar", None, "test-release", as_of=NOW)
    assert {p["key"] for p in facts["properties"]} >= {"email", "phones", "offices"}
    assert facts["sources"][0]["freshness"] == "fresh"


@pytest.mark.parametrize(
    "changes",
    [
        {"email": float("nan")},
        {"valid_from": "2026-11-01", "valid_until": "2026-10-01"},
        {"valid_until": "not-a-date"},
        {"normalization_metadata": {"evidence": {"source_urls": "not-a-list"}}},
    ],
)
def test_malformed_published_evidence_fails_closed(changes: dict[str, Any]) -> None:
    with pytest.raises(EvidenceUnavailable):
        reader([contact(**changes)]).get_office_facts("registrar", ["email"], "test-release")


def test_postgres_read_is_parameterized_read_only_and_deadline_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []
    kwargs_seen: dict[str, Any] = {}
    malicious_key = "office:'; DROP TABLE contacts; --"
    entity = office(keys=[malicious_key])

    class Cursor:
        def __init__(self, result: list[dict[str, Any]]) -> None:
            self.result = result

        def fetchall(self) -> list[dict[str, Any]]:
            return deepcopy(self.result)

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def execute(self, query: str, params: Any = None) -> Cursor:
            calls.append((query, params))
            if "dataset_versions v" in query:
                return Cursor(
                    [
                        {
                            "dataset_id": "dataset-uuid",
                            "dataset_version": "test-release",
                            "identity_hash": "identity-hash",
                            "registry": {
                                "schema_version": 1,
                                "entities": [entity],
                            },
                            "alias_sources": [],
                        }
                    ]
                )
            if "campus_contacts c" in query:
                return Cursor([contact(source_record_key=malicious_key)])
            return Cursor([])

    def connect(*args: Any, **kwargs: Any) -> Connection:
        kwargs_seen.update(kwargs)
        return Connection()

    monkeypatch.setattr(psycopg, "connect", connect)
    result = PostgresEntityFacts("postgresql://example.invalid/test").get_office_facts(
        "registrar",
        ["email"],
        "test-release",
    )
    assert result["evidence_count"] == 1
    assert kwargs_seen["connect_timeout"] == 3
    assert "statement_timeout=2000" in kwargs_seen["options"]
    assert "default_transaction_read_only=on" in kwargs_seen["options"]
    assert calls[0][0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    contact_query, params = next(call for call in calls if "campus_contacts c" in call[0])
    assert malicious_key not in contact_query
    assert malicious_key in params[0]
    assert params[2] == 129
    assert kwargs_seen["tcp_user_timeout"] == 4_000
    statement_limits = [int(params[0][:-2]) for query, params in calls if "set_config" in query]
    assert len(statement_limits) == 2
    assert all(0 < timeout <= 2_000 for timeout in statement_limits)
    ticks = iter([0.0, 5.0])
    monkeypatch.setattr(
        "rockygpt_brain.retrieval.postgres.time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )
    calls.clear()
    with pytest.raises(EvidenceUnavailable, match="read deadline"):
        PostgresEntityFacts("postgresql://example.invalid/test").search_offices("Registrar")
    assert len(calls) == 1  # No release/contact query starts after time is exhausted.


def test_database_failure_is_sanitized_and_readiness_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise psycopg.OperationalError("password=private database details")

    monkeypatch.setattr(psycopg, "connect", unavailable)
    service = PostgresEntityFacts("postgresql://example.invalid/test")
    with pytest.raises(EvidenceUnavailable, match="temporarily unavailable") as exc:
        service.search_offices("Registrar")
    assert "private" not in str(exc.value)
    assert service.readiness() == {"ready": False}
