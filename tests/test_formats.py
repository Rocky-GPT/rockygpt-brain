"""Exact formats must match the request, source fields and coverage independently."""

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.data import SearchFilters, SearchQuery
from rockygpt_brain.engine import run_turn
from rockygpt_brain.formats import combine_exact, exact_search
from test_engine import answer, review, tools
from test_schedules import NOW, output
from test_schedules import record as trip


def messages(text: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=text)]


def menu(name: str = "Lentil stew", **changes: Any) -> dict[str, Any]:
    fields = {
        "name": name,
        "meal": "Dinner",
        "venue": "Garden Hall",
        "vegan": True,
        "vegetarian": True,
        "allergens": [],
        "calories": 240,
    }
    return {
        "id": "menu:" + name,
        "entity_id": "dish:" + name,
        "collection": "menu",
        "source_key": "menu",
        "freshness": "fresh",
        "trust_tier": "official_primary",
        "url": "https://www.ramapo.edu/dining/",
        "title": "Published menu",
        "valid_from": str(NOW.date()),
        "valid_until": str(NOW.date()),
        "fields": fields,
        "coverage": {"fields": {key: "published" for key in fields}},
        **changes,
    }


def menu_query(**filters: Any) -> SearchQuery:
    return SearchQuery(
        collection="menu",
        date_from=NOW.date(),
        limit=100,
        filters=SearchFilters(meal="Dinner", **filters),
    )


def hours(schedule: str = "05:00 PM - 08:00 PM") -> dict[str, Any]:
    row = menu()
    row.update(collection="dining_hours", id="hours:garden", entity_id="venue:garden")
    row["fields"] = {
        "name": "Garden Hall",
        "day": "Wednesday",
        "schedule": schedule,
        "service_date": str(NOW.date()),
    }
    row["coverage"] = {"fields": {key: "published" for key in row["fields"]}}
    return row


def test_complete_dinner_returns_all_published_items_without_more_model_calls() -> None:
    question = "what is for dinner today"
    query = menu_query()
    client, data = Mock(), Mock()
    client.create.return_value = tools(
        SimpleNamespace(
            type="function_call",
            name="search_campus",
            call_id="menu",
            arguments=json.dumps({**query.model_dump(mode="json"), "request_text": question}),
        )
    )
    data.search.return_value = output(menu(), menu("Roasted squash"))
    result = run_turn(messages(question), client=client, data=data, model="test", now=NOW)
    assert result["status"] == "answered"
    assert "Lentil stew" in result["answer"] and "Roasted squash" in result["answer"]
    assert "Garden Hall" in result["answer"] and str(NOW.date()) in result["answer"]
    assert result["metrics"]["responseMode"] == "exact_records"
    assert client.create.call_count == 1 and result["metrics"]["reviewCalls"] == 0


def resolved_menu() -> dict[str, Any]:
    result = output(menu())
    result["coverage"] = {
        "name_resolution": {
            "field": "venue",
            "query": "Garden",
            "canonical_name": "Garden Hall",
            "basis": "unique_published_name_prefix",
        }
    }
    return result


def test_code_resolved_short_venue_name_can_use_exact_menu_format() -> None:
    question = "What vegan options are on Garden's dinner menu today?"
    query = menu_query(vegan=True).model_copy(update={"query": "Garden"})
    piece = exact_search(question, messages(question), query, resolved_menu(), NOW)
    assert piece is not None and piece.complete
    assert "Garden Hall" in piece.answer.parts[0].text
    assert "Lentil stew" in piece.answer.parts[0].text
    # No proof of name resolution: even complete keyword results remain prose.
    assert exact_search(question, messages(question), query, output(menu()), NOW) is None


@pytest.mark.parametrize(
    "change",
    [
        {"canonical_name": "Other Hall"},
        {"query": "Garden Hall"},
        {"basis": "model_guess"},
        {"field": "name"},
    ],
)
def test_inconsistent_name_resolution_cannot_exempt_prose(change: dict[str, str]) -> None:
    question = "What is for dinner at Garden today?"
    query = menu_query().model_copy(update={"query": "Garden"})
    result = resolved_menu()
    result["coverage"]["name_resolution"].update(change)
    assert exact_search(question, messages(question), query, result, NOW) is None


@pytest.mark.parametrize(
    "question",
    [
        "What safe vegan options are on Garden's dinner menu today?",
        "What vegan options are on Other Hall's dinner menu today?",
        "What vegan options are on Garden's dinner menu tomorrow?",
    ],
)
def test_resolved_name_does_not_remove_other_request_qualifiers(question: str) -> None:
    query = menu_query(vegan=True).model_copy(update={"query": "Garden"})
    assert exact_search(question, messages(question), query, resolved_menu(), NOW) is None


@pytest.mark.parametrize(
    "question",
    [
        "what is for dinner tomorrow",
        "what is for lunch today",
        "what is for dinner at Other Hall today",
        "what is safe for dinner today",
        "what is cheap for dinner today",
        "what is gluten free for dinner today",
        "what is for dinner next Wednesday",
        "what is for dinner today and tomorrow",
    ],
)
def test_unconsumed_qualifiers_never_use_exact_exemption(question: str) -> None:
    assert exact_search(question, messages(question), menu_query(), output(menu()), NOW) is None


@pytest.mark.parametrize(
    "change",
    [
        {"freshness": "stale"},
        {"trust_tier": "unknown"},
        {"coverage": {}},
        {"valid_from": "2026-09-15"},
        {"valid_until": "2026-09-15"},
        {"content_truncated": True},
        {"entity_id": None},
    ],
)
def test_unverified_menu_facts_do_not_bypass_review(change: dict[str, Any]) -> None:
    question = "what is for dinner today"
    assert (
        exact_search(question, messages(question), menu_query(), output(menu(**change)), NOW)
        is None
    )


def test_dietary_request_and_source_flag_must_both_match() -> None:
    question = "what vegan dinner is available today"
    assert exact_search(question, messages(question), menu_query(), output(menu()), NOW) is None
    query = menu_query(vegan=True)
    assert exact_search(question, messages(question), query, output(menu()), NOW) is not None
    row = menu()
    row["fields"]["vegan"] = False
    assert exact_search(question, messages(question), query, output(row), NOW) is None


def test_conflicting_identity_is_not_chosen() -> None:
    first, other = menu(), menu()
    other["fields"]["name"] = "Different dish"
    question = "what is for dinner today"
    assert (
        exact_search(question, messages(question), menu_query(), output(first, other), NOW) is None
    )


def test_partial_menu_is_not_presented_as_complete_and_keeps_qualification() -> None:
    question = "dinner allergens today"
    rows = {**output(menu()), "total_matches": 7, "truncated": True}
    piece = exact_search(question, messages(question), menu_query(), rows, NOW)
    assert piece is not None and not piece.complete
    assert combine_exact(messages(question), [piece], fallback=False) is None
    result = combine_exact(messages(question), [piece], fallback=True)
    assert result is not None and result.status == "partial"
    text = " ".join(part.text for part in result.parts)
    assert "not specified" in text and "cross-contact" in text and "Only part" in text


@pytest.mark.parametrize(
    "question",
    [
        "Do not tell me what is for dinner today",
        "Is what is for dinner today safe for my allergy?",
        "what is for dinner today if I am allergic to nuts",
    ],
)
def test_quote_cannot_omit_qualifiers_from_its_atomic_request(question: str) -> None:
    assert (
        exact_search(
            "what is for dinner today", messages(question), menu_query(), output(menu()), NOW
        )
        is None
    )


def test_unanswered_multipart_request_prevents_early_return() -> None:
    quote = "what is for dinner today"
    request = quote + " and how do I appeal a parking ticket?"
    piece = exact_search(quote, messages(request), menu_query(), output(menu()), NOW)
    assert piece is not None
    assert combine_exact(messages(request), [piece], fallback=False) is None
    result = combine_exact(messages(request), [piece], fallback=True)
    assert result is not None and result.status == "partial"
    assert "Lentil stew" in result.parts[0].text


def test_rejected_prose_retains_only_independent_facts_and_limitations() -> None:
    quote = "dinner allergens today"
    request = quote + " and how do I appeal a parking ticket?"
    client, data = Mock(), Mock()
    query = menu_query()
    client.create.side_effect = [
        tools(
            SimpleNamespace(
                type="function_call",
                name="search_campus",
                call_id="menu",
                arguments=json.dumps({**query.model_dump(mode="json"), "request_text": quote}),
            )
        ),
        answer("You can always ignore parking tickets."),
        review("unsupported_claim"),
    ]
    data.search.return_value = output(menu())
    result = run_turn(messages(request), client=client, data=data, model="test", now=NOW)
    assert result["status"] == "partial"
    assert "Lentil stew" in result["answer"] and "cross-contact" in result["answer"]
    assert "ignore parking tickets" not in result["answer"]
    assert client.create.call_count == 3


@pytest.mark.parametrize(
    ("question", "schedule", "expected"),
    [
        ("when does Garden Hall close today", "05:00 PM - 08:00 PM", "8:00 PM"),
        ("when does Garden Hall close today", "05:00 PM - 01:00 AM", "2026-09-17"),
        ("when does Garden Hall open and close today", "05:00 PM - 08:00 PM", "05:00 PM"),
        ("Garden Hall hours today", "Closed (seasonal closure)", "Closed (seasonal closure)"),
    ],
)
def test_exact_hours_keep_requested_boundaries(question: str, schedule: str, expected: str) -> None:
    query = SearchQuery(collection="dining_hours", date_from=NOW.date())
    piece = exact_search(question, messages(question), query, output(hours(schedule)), NOW)
    assert piece is not None and expected in piece.answer.parts[0].text


def test_regular_hours_keep_exception_limit_and_open_now_needs_more_evidence() -> None:
    row = hours()
    row.pop("valid_from")
    row.pop("valid_until")
    query = SearchQuery(collection="dining_hours", date_from=NOW.date())
    question = "Garden Hall hours today"
    piece = exact_search(question, messages(question), query, output(row), NOW)
    assert piece is not None and "exceptions" in piece.answer.parts[-1].text
    question = "is Garden Hall open now"
    assert exact_search(question, messages(question), query, output(row), NOW) is None


def test_next_departure_requires_route_origin_and_single_requested_extremum() -> None:
    row = trip(1, "5:30 PM", "5:40 PM", "6:00 PM")
    query = SearchQuery(collection="shuttle", date_from=NOW.date(), limit=50)
    question = "when is the next Station route shuttle from campus today"
    piece = exact_search(question, messages(question), query, output(row), NOW)
    assert piece is not None and "5:30 PM" in piece.answer.parts[0].text
    for request in [
        "next shuttle",
        question.replace("next", "next and last"),
        question.replace("campus", "Station"),
    ]:
        assert exact_search(request, messages(request), query, output(row), NOW) is None


def test_failed_format_does_not_mutate_evidence() -> None:
    rows = output(menu())
    before = deepcopy(rows)
    question = "what is for dinner today"
    exact_search(question, messages(question), menu_query(), rows, NOW)
    assert rows == before


def test_complete_meal_larger_than_one_citation_group_stays_complete() -> None:
    question = "what is for dinner today"
    rows = output(*(menu(f"Dish {index}") for index in range(75)))
    piece = exact_search(question, messages(question), menu_query(), rows, NOW)
    assert piece is not None and piece.complete
    result = combine_exact(messages(question), [piece], fallback=False)
    assert result is not None and result.status == "answered"
    assert sum(len(part.evidence_ids) for part in result.parts) == 75
    assert "Dish 74" in result.parts[-1].text


def test_oversized_exact_menu_continues_to_reviewed_answer() -> None:
    question = "what is for dinner today"
    client, data = Mock(), Mock()
    rows = output(*(menu(f"Dish {index}: " + "x" * 160) for index in range(75)))
    query = menu_query()
    client.create.side_effect = [
        tools(
            SimpleNamespace(
                type="function_call",
                name="search_campus",
                call_id="menu",
                arguments=json.dumps({**query.model_dump(mode="json"), "request_text": question}),
            )
        ),
        answer("The published dinner includes Dish 0.", "campus_fact", [rows["records"][0]["id"]]),
        review(),
    ]
    data.search.return_value = rows
    result = run_turn(messages(question), client=client, data=data, model="test", now=NOW)
    assert result["metrics"]["responseMode"] == "reviewed_prose"
    assert client.create.call_count == 3


@pytest.mark.parametrize("verdict", ["supported", "unsupported_claim"])
def test_multipart_keeps_code_facts_and_reviews_only_new_prose(verdict: str) -> None:
    quote = "dinner allergens today"
    request = quote + " and what is my current GPA?"
    client, data = Mock(), Mock()
    client.create.side_effect = [
        tools(
            SimpleNamespace(
                type="function_call",
                name="search_campus",
                call_id="menu",
                arguments=json.dumps(
                    {**menu_query().model_dump(mode="json"), "request_text": quote}
                ),
            )
        ),
        answer("I cannot access your personal academic record."),
        review(verdict),
    ]
    data.search.return_value = output(menu())
    result = run_turn(messages(request), client=client, data=data, model="test", now=NOW)
    assert "Lentil stew" in result["answer"] and "cross-contact" in result["answer"]
    assert client.create.call_count == 3
    writing = client.create.call_args_list[1].kwargs
    assert "Write only the remaining" in writing["instructions"]
    assert quote in writing["instructions"]
    checking = json.loads(client.create.call_args_list[2].kwargs["input"])
    assert len(checking["candidate"]["parts"]) == 1
    assert checking["verified_prefix"][0]["kind"] == "campus_fact"
    assert "Lentil stew" in checking["verified_prefix"][0]["text"]
    if verdict == "supported":
        assert "personal academic record" in result["answer"]
        assert result["metrics"]["responseMode"] == "exact_plus_reviewed"
    else:
        assert "personal academic record" not in result["answer"]
        assert result["metrics"]["responseMode"] == "safe_fallback"
