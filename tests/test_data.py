from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from rockygpt_brain.data import CampusData, ReadQuery, SearchQuery

NOW = datetime(2026, 9, 4, 23, tzinfo=UTC)


@pytest.fixture
def data() -> CampusData:
    repository = object.__new__(CampusData)
    repository.now = NOW
    repository.today = date(2026, 9, 4)
    repository.dataset = {"id": "dataset-one", "version": "published-one", "activated_at": NOW}
    repository.sources = {
        "source": {
            "id": "source",
            "source_key": "academic-programs",
            "title": "Ramapo College",
            "canonical_url": "https://www.ramapo.edu/",
            "trust_tier": "official_primary",
            "freshness_sla_hours": 24,
            "provenance_status": "success",
            "completed_at": NOW,
        }
    }
    repository._cache = {}
    repository._artifacts = {}
    repository._seen = {}
    return repository


def record(
    data: CampusData,
    collection: str,
    title: str,
    fields: dict[str, Any],
    key: str = "one",
    **metadata: Any,
) -> dict[str, Any]:
    result = data._evidence(
        collection,
        {
            "id": key,
            "source_id": "source",
            "collected_at": NOW,
            **metadata,
        },
        fields,
        title,
    )
    assert result is not None
    return result


def test_postgres_is_read_only_bounded_and_dataset_pinned() -> None:
    connection = MagicMock()
    responses = [
        [{"id": "dataset-one", "version": "published-one", "activated_at": NOW}],
        [
            {
                "id": "source",
                "source_key": "housing",
                "canonical_url": "https://www.ramapo.edu/",
                "title": "Housing",
                "trust_tier": "official_primary",
                "freshness_sla_hours": 168,
                "provenance_status": "success",
                "completed_at": NOW,
            }
        ],
        [],
    ]
    connection.cursor.return_value.__enter__.return_value.fetchall.side_effect = responses
    with patch("rockygpt_brain.data.psycopg.connect", return_value=connection) as connect:
        repository = CampusData("postgresql://configured", NOW)
        repository.search(SearchQuery(collection="contacts", query="'; DROP TABLE sources; --"))
        repository.close()
    assert connect.call_args.kwargs["autocommit"] is True
    assert connection.read_only is True
    assert connect.call_args.kwargs["sslrootcert"]
    assert "options" not in connect.call_args.kwargs
    all_calls = connection.cursor.return_value.__enter__.return_value.execute.call_args_list
    assert sum("set_config('statement_timeout'" in str(call.args[0]) for call in all_calls) == 3
    assert connection.transaction.call_count == 3
    calls = [call for call in all_calls if "set_config(" not in str(call.args[0])]
    assert calls[1].args[1] == ("dataset-one",)
    assert calls[2].args[1] == ("dataset-one",)
    assert "DROP" not in str(calls[2].args[0])
    assert sum("status = 'active'" in str(call.args[0]) for call in calls) == 1
    connection.close.assert_called_once()


def test_no_dataset_closes_connection() -> None:
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = []
    with patch("rockygpt_brain.data.psycopg.connect", return_value=connection):
        with pytest.raises(RuntimeError, match="No active"):
            CampusData("postgresql://configured", NOW).readiness()
    connection.close.assert_called_once()


def test_rejects_non_allowlisted_collection_and_bad_ranges() -> None:
    with pytest.raises(ValidationError):
        SearchQuery(collection="sources; DROP TABLE users")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SearchQuery(collection="events", date_from=date(2026, 9, 5), date_to=date(2026, 9, 4))
    with pytest.raises(ValidationError):
        SearchQuery(collection="events", limit=10000)


def test_menu_dates_do_not_substitute_another_day_or_undated_food(data: CampusData) -> None:
    data._cache["menu"] = [
        record(
            data,
            "menu",
            "Tofu",
            {"name": "Tofu", "vegan": True},
            valid_from="2026-09-04",
            valid_until="2026-09-04",
        ),
        record(
            data,
            "menu",
            "Pizza",
            {"name": "Pizza"},
            "later",
            valid_from="2026-09-09",
            valid_until="2026-09-09",
        ),
        record(data, "menu", "Unknown", {"name": "Unknown"}, "unknown"),
    ]
    current = data.search(SearchQuery(collection="menu", date_from=date(2026, 9, 4)))
    missing = data.search(SearchQuery(collection="menu", date_from=date(2026, 9, 7)))
    assert [r["title"] for r in current["records"]] == ["Tofu"]
    assert missing["records"] == []
    assert missing["status"] == "no_match"  # Missing menu is never proof of closure.


def test_seasonal_closure_overrides_weekly_schedule_and_read_keeps_date(data: CampusData) -> None:
    data._cache["dining_hours"] = [
        record(data, "dining_hours", "Dunkin'", {"day": "Monday", "schedule": "7:30 AM - 3 PM"}),
        record(
            data,
            "dining_hours",
            "Dunkin'",
            {"day": "Monday", "schedule": "Closed"},
            "closure",
            valid_from="2026-09-06",
            valid_until="2026-09-07",
        ),
    ]
    result = data.search(SearchQuery(collection="dining_hours", date_from=date(2026, 9, 7)))
    assert len(result["records"]) == 1
    assert result["records"][0]["fields"]["schedule"] == "Closed"
    expanded = data.read(ReadQuery(ids=[result["records"][0]["id"]]))
    assert expanded["records"][0]["fields"]["service_date"] == "2026-09-07"
    later = data.search(SearchQuery(collection="dining_hours", date_from=date(2026, 9, 14)))
    assert later["records"][0]["fields"]["schedule"] == "7:30 AM - 3 PM"


def test_weekend_shuttle_excludes_weekday_and_unknown_schedules(data: CampusData) -> None:
    data._cache["shuttle"] = [
        record(data, "shuttle", "Ramsey Route 17", {"service_day": "weekday"}),
        record(data, "shuttle", "Saturday Roadrunner", {"service_day": "saturday"}, "sat"),
        record(data, "shuttle", "Unknown Service", {}, "unknown"),
    ]
    result = data.search(SearchQuery(collection="shuttle", date_from=date(2026, 9, 5)))
    assert [r["title"] for r in result["records"]] == ["Saturday Roadrunner"]
    assert "holiday" in result["records"][0]["limitations"][0]


def test_event_dates_use_campus_timezone_and_historical_calendar_remains(data: CampusData) -> None:
    data._cache["events"] = [
        record(data, "events", "CPB Carnival", {"starts_at": "2026-09-05T01:00:00+00:00"})
    ]
    result = data.search(
        SearchQuery(collection="events", date_from=date(2026, 9, 4), date_to=date(2026, 9, 4))
    )
    assert len(result["records"]) == 1
    data._cache["calendar"] = [
        record(
            data,
            "calendar",
            "Full Semester Add/Drop",
            {
                "starts_at": datetime(2026, 9, 1, 4, tzinfo=UTC),
                "session": "Full Semester",
            },
        )
    ]
    historical = data.search(
        SearchQuery(collection="calendar", date_from=date(2026, 9, 1), date_to=date(2026, 9, 1))
    )
    assert len(historical["records"]) == 1
    json.dumps(historical)  # Tool results must be JSON-safe, including PostgreSQL datetimes.


def test_expired_critical_facts_are_not_current(data: CampusData) -> None:
    data._cache["critical_facts"] = [
        record(
            data,
            "critical_facts",
            "spring finals",
            {
                "fact_value": "May 6, 2026",
            },
            valid_from="2025-08-01",
            valid_until="2026-05-31",
        )
    ]
    assert not data.search(SearchQuery(collection="critical_facts"))["records"]
    assert data.search(SearchQuery(collection="critical_facts", date_from=date(2026, 5, 1)))[
        "records"
    ]


def test_freshness_ages_from_collection_not_activation_and_rejects_unknown_source(
    data: CampusData,
) -> None:
    stale = record(data, "contacts", "Registrar", {}, collected_at=NOW - timedelta(hours=25))
    unknown = record(data, "contacts", "Registrar", {}, collected_at=None)
    future = record(data, "contacts", "Registrar", {}, collected_at=NOW + timedelta(days=2))
    assert stale["freshness"] == "stale"
    assert unknown["freshness"] == "unknown"
    assert future["freshness"] == "unknown"
    assert stale["limitations"]
    assert data._evidence("contacts", {"source_id": "community"}, {}, "Spoofed") is None


def test_keyword_ranking_prefers_title_and_ignores_internal_ids(data: CampusData) -> None:
    data._cache["courses"] = [
        record(data, "courses", "CMPS 147 — Computer Science I", {"code": "CMPS 147"}),
        record(data, "courses", "Other course", {"description": "CMPS 147"}, "other"),
        record(data, "courses", "Unrelated", {"name": "Unrelated"}, "CMPS147-secret"),
    ]
    result = data.search(SearchQuery(collection="courses", query="CMPS147", limit=1))
    assert result["records"][0]["title"] == "CMPS 147 — Computer Science I"
    assert result["truncated"] is True
    assert result["total_matches"] == 2
    assert data.search(SearchQuery(collection="courses", query="secret"))["records"] == []


def test_false_diet_flags_do_not_match_vegan(data: CampusData) -> None:
    data._cache["menu"] = [
        record(data, "menu", "Tofu", {"vegan": True}, valid_from="2026-09-04"),
        record(data, "menu", "Chicken", {"vegan": False}, "chicken", valid_from="2026-09-04"),
    ]
    assert [
        r["title"]
        for r in data.search(
            SearchQuery(collection="menu", query="vegan", date_from=date(2026, 9, 4))
        )["records"]
    ] == ["Tofu"]


def test_document_read_stays_on_same_page_heading_and_pinned_dataset(data: CampusData) -> None:
    document = record(data, "documents", "Guest Policy", {})
    document.update(_document_id="document-one", _chunk_index=10, content="Initial passage")
    data._seen[document["id"]] = document
    fetch = MagicMock(return_value=[{"content": "Same page expanded details"}])
    with patch.object(data, "_fetch", fetch):
        result = data.read(ReadQuery(ids=[document["id"], "documents:invented"]))
    query, parameters = fetch.call_args.args
    assert "canonicalUrl" in query and "headingPath" in query
    assert parameters == ("dataset-one", "document-one", 9, 13, document["url"], "Guest Policy")
    assert result["records"][0]["content"] == "Same page expanded details"
    assert result["records"][0]["url"] == document["url"]
    assert result["missing_ids"] == ["documents:invented"]
    assert "_document_id" not in result["records"][0]


def test_course_artifacts_preserve_credits_and_program_requirement_rules(data: CampusData) -> None:
    data._artifacts["courses"] = {
        "CMPS 147": {
            "name": "Computer Science I",
            "code": "CMPS 147",
            "credits": {"min": 0, "max": 4},
            "description": "Programming foundations",
            "internalHash": "never-search-this",
        }
    }
    data._artifacts["programs"] = {
        "schools": [
            {
                "majors": [
                    {
                        "name": "Computer Science BS",
                        "catalogUrl": "https://catalog.ramapo.edu/programs/CS",
                        "requirements": [
                            {
                                "section": "Core",
                                "rule": {
                                    "condition": "completedAllOf",
                                    "items": [
                                        {"codes": [{"code": "CMPS 147"}], "logic": "and"},
                                    ],
                                },
                            }
                        ],
                    }
                ]
            }
        ]
    }
    result = data.search(SearchQuery(collection="courses", query="CMPS147"))
    assert result["records"][0]["fields"]["credits"] == {"min": 0, "max": 4}
    assert result["records"][0]["url"] == "https://www.ramapo.edu/"
    assert "internalHash" not in result["records"][0]["fields"]
    assert "content" not in result["records"][0]
    requirements = data.search(
        SearchQuery(collection="program_requirements", query="Computer Science")
    )
    assert requirements["records"][0]["fields"]["rule"]["condition"] == "completedAllOf"


def test_large_nested_payload_is_bounded_and_explicitly_truncated(data: CampusData) -> None:
    data._cache["program_requirements"] = [
        record(
            data,
            "program_requirements",
            "Core",
            {
                "rule": {"condition": "completedAnyOf", "items": ["long course " * 100] * 500},
            },
        )
    ]
    result = data.search(SearchQuery(collection="program_requirements"))
    assert result["records"][0]["content_truncated"] is True
    assert len(json.dumps(result)) < 7000
    expanded = data.read(ReadQuery(ids=[result["records"][0]["id"]]))
    assert len(json.dumps(expanded)) < 27000
    assert expanded["records"][0]["content_truncated"] is True


def test_general_help_can_construct_and_close_without_database() -> None:
    with patch(
        "rockygpt_brain.data.psycopg.connect", side_effect=RuntimeError("offline")
    ) as connect:
        repository = CampusData("postgresql://unavailable", NOW)
        repository.close()
        connect.assert_not_called()
        with pytest.raises(RuntimeError, match="offline"):
            repository.readiness()


@pytest.mark.parametrize(
    "uri,environment,expected",
    [
        ("postgresql://configured?sslmode=verify-full", {}, "bundle.pem"),
        ("postgresql://configured?sslmode=verify-full&sslrootcert=custom.pem", {}, "custom.pem"),
        ("postgresql://configured?sslmode=verify-full", {"PGSSLROOTCERT": "environment.pem"}, None),
    ],
)
def test_tls_verification_preserves_explicit_roots(
    uri: str, environment: dict[str, str], expected: str | None
) -> None:
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.side_effect = [
        [{"id": "dataset", "version": "one", "activated_at": NOW}],
        [],
    ]
    with (
        patch.dict("os.environ", environment, clear=True),
        patch("rockygpt_brain.data.certifi.where", return_value="bundle.pem"),
        patch("rockygpt_brain.data.psycopg.connect", return_value=connection) as connect,
    ):
        repository = CampusData(uri, NOW)
        repository.readiness()
        repository.close()
    assert connect.call_args.kwargs["sslmode"] == "verify-full"
    assert connect.call_args.kwargs.get("sslrootcert") == expected


def test_shuttle_browse_uses_published_sequence_not_random_ids(data: CampusData) -> None:
    data._cache["shuttle"] = [
        record(
            data,
            "shuttle",
            "Saturday Roadrunner",
            {
                "service_day": "saturday",
                "sequence": 2,
                "departure": "11 AM",
            },
            "aaa",
        ),
        record(
            data,
            "shuttle",
            "Saturday Roadrunner",
            {
                "service_day": "saturday",
                "sequence": 0,
                "departure": "9 AM",
            },
            "zzz",
        ),
    ]
    result = data.search(SearchQuery(collection="shuttle", date_from=date(2026, 9, 5)))
    assert [r["fields"]["departure"] for r in result["records"]] == ["9 AM", "11 AM"]


def test_menu_venue_and_url_come_from_published_metadata(data: CampusData) -> None:
    data._artifacts["menu-context"] = {
        "content": '---\nsource_url: "https://dining.example.edu/cedar"\n---\n# Cedar Hall Menu\n'
    }
    meals = [
        record(
            data, "menu", "Roasted Tofu", {"meal": "Dinner", "vegan": True}, valid_from="2026-09-04"
        )
    ]
    data._enrich("menu", meals)
    data._cache["menu"] = meals
    result = data.search(
        SearchQuery(collection="menu", query="Cedar vegan dinner", date_from=date(2026, 9, 4))
    )
    assert result["records"][0]["fields"]["venue"] == "Cedar Hall"
    assert result["records"][0]["url"] == "https://dining.example.edu/cedar"
    assert "content" not in result["records"][0]


def test_shuttle_normalization_preserves_round_trip_endpoint_meaning(data: CampusData) -> None:
    rows = [
        {
            "id": "trip",
            "source_id": "source",
            "collected_at": NOW,
            "valid_from": None,
            "valid_until": None,
            "sequence": 7,
            "departure": "5:25 PM",
            "arrival": "6:35 PM",
            "route": "Saturday Roadrunner Express",
            "service_day": "saturday",
            "stops": [
                {"location": "Garden State Plaza", "time": "6:00 PM"},
                {"location": "Barnes & Noble", "time": "6:10 PM"},
                {"location": "Ramsey Square", "time": "6:25 PM"},
            ],
        }
    ]
    with patch.object(data, "_fetch", return_value=rows):
        result = data.search(SearchQuery(collection="shuttle", date_from=date(2026, 9, 5)))
    fields = result["records"][0]["fields"]
    assert fields["campus_departure"] == "5:25 PM"
    assert fields["campus_return"] == "6:35 PM"
    assert fields["stops"] == rows[0]["stops"]
    assert "listed order" in fields["stop_order"]
    assert "arrival" not in fields and "departure" not in fields


def test_data_deadline_caps_connection_and_statement_timeout() -> None:
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.side_effect = [
        [{"id": "dataset", "version": "one", "activated_at": NOW}],
        [],
    ]
    with (
        patch("rockygpt_brain.data.time.monotonic", return_value=100.0),
        patch("rockygpt_brain.data.psycopg.connect", return_value=connection) as connect,
    ):
        repository = CampusData("postgresql://configured", NOW)
        repository.deadline = 102.25
        repository.readiness()
        repository.close()
    assert connect.call_args.kwargs["connect_timeout"] == 3
    calls = connection.cursor.return_value.__enter__.return_value.execute.call_args_list
    timeout_calls = [call for call in calls if "set_config(" in str(call.args[0])]
    assert [call.args[1] for call in timeout_calls] == [("2250",), ("2250",)]


def test_exhausted_deadline_stops_before_connect_or_query(data: CampusData) -> None:
    with (
        patch("rockygpt_brain.data.time.monotonic", return_value=100.0),
        patch("rockygpt_brain.data.psycopg.connect") as connect,
    ):
        repository = CampusData("postgresql://configured", NOW)
        repository.deadline = 99.0
        with pytest.raises(TimeoutError, match="budget exhausted"):
            repository.readiness()
        connect.assert_not_called()
        data.deadline = 99.0
        with pytest.raises(TimeoutError, match="budget exhausted"):
            data._fetch("SELECT 1")


def test_dining_meal_labels_require_exact_source_schedule_match(data: CampusData) -> None:
    data._artifacts["dining-hours"] = {
        "nested": [
            {
                "name": "Cedar Hall",
                "openingHours": {
                    "standardHours": [
                        {
                            "days": [{"value": "Friday"}],
                            "hours": [
                                {
                                    "label": "Dinner",
                                    "startTime": {"hour": "05", "minute": "00", "period": "PM"},
                                    "finishTime": {"hour": "08", "minute": "00", "period": "PM"},
                                }
                            ],
                        }
                    ]
                },
            }
        ]
    }
    records = [
        record(
            data,
            "dining_hours",
            "Cedar Hall",
            {
                "name": "Cedar Hall",
                "day": "Friday",
                "schedule": "05:00 PM - 08:00 PM",
            },
        ),
        record(
            data,
            "dining_hours",
            "Cedar Hall",
            {
                "name": "Cedar Hall",
                "day": "Friday",
                "schedule": "05:00 PM - 07:00 PM",
            },
            "different-time",
        ),
        record(
            data,
            "dining_hours",
            "Cedar Hall",
            {
                "name": "Cedar Hall",
                "day": "Saturday",
                "schedule": "05:00 PM - 08:00 PM",
            },
            "different-day",
        ),
    ]
    data._enrich("dining_hours", records)
    assert records[0]["fields"]["periods"] == [
        {"label": "Dinner", "start": "05:00 PM", "end": "08:00 PM"},
    ]
    assert "periods" not in records[1]["fields"]
    assert "periods" not in records[2]["fields"]


@pytest.mark.parametrize(
    "collection", ["menu", "campus_hours", "dining_hours", "shuttle", "events"]
)
def test_date_dependent_search_rejects_implicit_today(collection: str) -> None:
    with pytest.raises(ValidationError, match="date_from is required"):
        SearchQuery.model_validate({"collection": collection, "query": "Saturday"})
    valid = SearchQuery.model_validate(
        {
            "collection": collection,
            "query": "",
            "date_from": "2026-09-05",
        }
    )
    assert valid.date_from == date(2026, 9, 5)


def test_no_match_discovers_published_names_without_admitting_them_as_evidence(
    data: CampusData,
) -> None:
    club = record(data, "clubs", "Garden Club", {"name": "Garden Club"}, "garden")
    data._cache["clubs"] = [club]
    data._seen = {}
    missing = data.search(SearchQuery(collection="clubs", query="ecology"))
    assert missing["status"] == "no_match"
    assert missing["records"] == []
    assert missing["discovery_titles"] == ["Garden Club"]
    assert data.read(ReadQuery(ids=[club["id"]]))["records"] == []
    found = data.search(SearchQuery(collection="clubs", query="Garden Club"))
    assert found["records"][0]["id"] == club["id"]
    assert found["records"][0]["source_key"] == "academic-programs"


def test_discovery_respects_date_scope_and_collection_size(data: CampusData) -> None:
    data._cache["events"] = [
        record(data, "events", "Current Event", {"starts_at": "2026-09-04T17:00:00-04:00"}),
        record(data, "events", "Future Event", {"starts_at": "2026-10-01T17:00:00-04:00"}, "later"),
    ]
    result = data.search(
        SearchQuery(
            collection="events",
            query="unmatched",
            date_from=date(2026, 9, 4),
            date_to=date(2026, 9, 4),
        )
    )
    assert result["discovery_titles"] == ["Current Event"]
    data._cache["courses"] = [
        record(data, "courses", f"Course {i}", {"code": str(i)}, str(i)) for i in range(301)
    ]
    assert (
        data.search(SearchQuery(collection="courses", query="unmatched"))["discovery_titles"] == []
    )


@pytest.mark.parametrize("website", ["http://www.ramapo.edu/group/", "javascript:alert(1)"])
def test_uncitable_record_website_uses_existing_official_source_url(
    data: CampusData,
    website: str,
) -> None:
    result = data._evidence(
        "clubs",
        {"id": "group", "source_id": "source", "collected_at": NOW},
        {"name": "Campus Group", "website_url": website},
        "Campus Group",
        website,
    )
    assert result is not None
    assert result["url"] == data.sources["source"]["canonical_url"]
    assert result["fields"]["website_url"] == website
