"""Exact formats must match the request, source fields and coverage independently."""

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from rockygpt_brain.campus.formats import (
    FAILURE_HELP,
    SAFETY_NET,
    combine_exact,
    exact_search,
    independent_quote,
)
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.retrieval.data import SearchFilters, SearchQuery
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
    question = "list the dinner menu today"
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
    question = "list the dinner menu today"
    assert (
        exact_search(question, messages(question), menu_query(), output(menu(**change)), NOW)
        is None
    )


def test_dietary_request_and_source_flag_must_both_match() -> None:
    question = "what vegan dinner menu is available today"
    assert exact_search(question, messages(question), menu_query(), output(menu()), NOW) is None
    query = menu_query(vegan=True)
    assert exact_search(question, messages(question), query, output(menu()), NOW) is not None
    row = menu()
    row["fields"]["vegan"] = False
    assert exact_search(question, messages(question), query, output(row), NOW) is None


def test_conflicting_identity_is_not_chosen() -> None:
    first, other = menu(), menu()
    other["fields"]["name"] = "Different dish"
    question = "list the dinner menu today"
    assert (
        exact_search(question, messages(question), menu_query(), output(first, other), NOW) is None
    )


def test_partial_menu_is_not_presented_as_complete_and_keeps_qualification() -> None:
    question = "dinner menu allergens today"
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
            "list the dinner menu today", messages(question), menu_query(), output(menu()), NOW
        )
        is None
    )


def test_unanswered_multipart_request_prevents_early_return() -> None:
    quote = "list the dinner menu today"
    request = quote + " and how do I appeal a parking ticket?"
    piece = exact_search(quote, messages(request), menu_query(), output(menu()), NOW)
    assert piece is not None
    assert combine_exact(messages(request), [piece], fallback=False) is None
    result = combine_exact(messages(request), [piece], fallback=True)
    assert result is not None and result.status == "partial"
    assert "Lentil stew" in result.parts[0].text


def test_rejected_prose_retains_only_independent_facts_and_limitations() -> None:
    quote = "dinner menu allergens today"
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


@pytest.mark.parametrize("schedule", ["Hours unavailable", "Lunch: Hours unavailable"])
def test_exact_hours_never_state_the_unverified_placeholder(schedule: str) -> None:
    # A Research Help Desk answer once read "Published hours ...: Hours unavailable."
    query = SearchQuery(collection="dining_hours", date_from=NOW.date())
    question = "Garden Hall hours today"
    assert exact_search(question, messages(question), query, output(hours(schedule)), NOW) is None


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
        question.replace("next", "next and last"),
        question.replace("campus", "Station"),
        "when is the next shuttle to campus",
    ]:
        assert exact_search(request, messages(request), query, output(row), NOW) is None


def test_next_shuttle_without_a_route_answers_every_route_from_campus() -> None:
    # A complete timetable fetched without a route filter covers every route.
    ramsey = trip(1, "5:30 PM", "5:40 PM", "N/A")
    ramsey["fields"]["route"] = "Ramsey Route 17"
    roadrunner = trip(2, "6:10 PM", "6:20 PM", "7:35 PM")
    roadrunner["fields"]["route"] = "Roadrunner Express"
    late = trip(0, "4:10 PM", "4:20 PM", "4:40 PM")
    late["fields"]["route"] = "Ramsey Route 17"
    query = SearchQuery(collection="shuttle", date_from=NOW.date(), limit=100)
    rows = output(ramsey, late, roadrunner)
    assert exact_search(
        "when's the next bus leaving campus?",
        messages("when's the next bus leaving campus?"), query, rows, NOW,
    ) is not None
    question = "when is the next shuttle"
    piece = exact_search(question, messages(question), query, rows, NOW)
    assert piece is not None and piece.complete and piece.answer.status == "answered"
    first = piece.answer.parts[0]
    assert first.text == (
        "The next published departures from campus on 2026-09-16 (America/New_York) are: "
        "Ramsey Route 17 at 4:10 PM; Roadrunner Express at 6:10 PM."
    )
    assert first.evidence_ids == ["shuttle:0", "shuttle:2"]

    at_six = NOW.replace(hour=18)
    rows = output(ramsey, late, roadrunner)
    piece = exact_search(question, messages(question), query, rows, at_six)
    assert piece is not None
    assert [part.kind for part in piece.answer.parts] == ["campus_fact", "limitation", "limitation"]
    assert piece.answer.parts[0].text.startswith(
        "The next published departure from campus on Roadrunner Express is 6:10 PM"
    )
    assert "on Ramsey Route 17 on 2026-09-16." in piece.answer.parts[1].text

    filtered = query.model_copy(update={"filters": SearchFilters(route="Ramsey Route 17")})
    rows = output(ramsey, late)
    assert exact_search(question, messages(question), filtered, rows, NOW) is None
    for request in ["when is the next shuttle from the train", "next shuttle to Garden State"]:
        assert exact_search(request, messages(request), query, rows, NOW) is None


def test_just_the_very_next_departure_names_every_route_leaving_then() -> None:
    # Q2 of the 09-29 conversation: two routes leave campus at 7:00, and GPT named one.
    ramsey = trip(1, "7:00 PM", "7:10 PM", "N/A")
    ramsey["fields"]["route"] = "Ramsey Route 17"
    roadrunner = trip(2, "7:00 PM", "7:15 PM", "7:35 PM")
    roadrunner["fields"]["route"] = "Roadrunner Express"
    query = SearchQuery(collection="shuttle", date_from=NOW.date(), limit=100)
    question = "Just give me the very next departure."
    piece = exact_search(question, messages(question), query, output(ramsey, roadrunner), NOW)
    assert piece is not None and piece.complete
    assert piece.answer.parts[0].text == (
        "The next published departures from campus on 2026-09-16 (America/New_York) are: "
        "Ramsey Route 17 at 7:00 PM; Roadrunner Express at 7:00 PM."
    )


def test_failed_format_does_not_mutate_evidence() -> None:
    rows = output(menu())
    before = deepcopy(rows)
    question = "list the dinner menu today"
    exact_search(question, messages(question), menu_query(), rows, NOW)
    assert rows == before


def test_complete_meal_larger_than_one_citation_group_stays_complete() -> None:
    question = "list the dinner menu today"
    rows = output(*(menu(f"Dish {index}") for index in range(75)))
    piece = exact_search(question, messages(question), menu_query(), rows, NOW)
    assert piece is not None and piece.complete
    result = combine_exact(messages(question), [piece], fallback=False)
    assert result is not None and result.status == "answered"
    assert sum(len(part.evidence_ids) for part in result.parts) == 75
    assert "Dish 74" in result.parts[-1].text


def test_oversized_exact_menu_continues_to_reviewed_answer() -> None:
    question = "list the dinner menu today"
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
    quote = "dinner menu allergens today"
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
    assert "Write only the remaining" in writing["input"][1]["content"]
    assert quote in writing["input"][1]["content"]
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


@pytest.mark.parametrize(
    "question",
    ["What's for dinner?", "what is for dinner today", "what vegan dinner is available today"],
)
def test_meal_overview_never_bypasses_review_with_a_component_dump(question: str) -> None:
    rows = output(menu("Sliced Tomato"), menu("Ginger"), menu("Garlic Grilled Chicken"))
    assert exact_search(question, messages(question), menu_query(), rows, NOW) is None


def test_explicit_full_menu_keeps_components() -> None:
    question = "list the full dinner menu today"
    rows = output(menu("Sliced Tomato"), menu("Garlic Grilled Chicken"))
    piece = exact_search(question, messages(question), menu_query(), rows, NOW)
    assert piece is not None
    assert "Sliced Tomato" in piece.answer.parts[0].text
    assert "Garlic Grilled Chicken" in piece.answer.parts[0].text


def test_danger_opening_covers_someone_else() -> None:
    # Jev's danger covers the student or someone else; "someone is unconscious" was told
    # "If you're in danger right now" (09-29), unlike the failure help beside it.
    assert "you or someone else" in SAFETY_NET["danger"] and "911" in SAFETY_NET["danger"]
    assert "you or someone else" in FAILURE_HELP


Q30 = ("Tell me the next shuttle, where the Registrar is, and what I should do if someone "
       "is unconscious.")


@pytest.mark.parametrize("quote", ["Tell me the next shuttle", "where the Registrar is"])
def test_a_comma_before_a_new_request_ends_a_part(quote: str) -> None:
    # Q30's shuttle quote was held dependent on its comma and went to GPT (09-29).
    assert independent_quote(quote, Q30)


@pytest.mark.parametrize(
    "text",
    [
        "Tell me the next shuttle, if it's running",
        "Tell me the next shuttle, when classes are cancelled",
        "Tell me the next shuttle, after 5 PM",
        "Tell me the next shuttle, please",
        # Relative clauses narrow the item; they are not new requests.
        "Tell me the next shuttle, which goes to the mall",
        "Tell me the next shuttle, which stops at the Plaza",
        "Tell me the next shuttle, where it stops",
        "Don't tell me the next shuttle, where the Registrar is",
        "Instead of the next shuttle, where the Registrar is",
    ],
)
def test_a_comma_before_a_qualifier_keeps_the_part_whole(text: str) -> None:
    quote = "Tell me the next shuttle" if text.startswith("Tell") else "the next shuttle"
    assert not independent_quote(quote, text)


@pytest.mark.parametrize(
    ("quote", "text"),
    [
        ("tell me the next shuttle", "After 5 PM, tell me the next shuttle"),
        ("tell me the next shuttle", "Other than Route 17, tell me the next shuttle"),
        ("tell me the next shuttle", "If it's running, tell me the next shuttle"),
        ("tell me the next shuttle", "When classes are cancelled, tell me the next shuttle"),
        ("what's the next shuttle?", "Not counting Route 17, what's the next shuttle?"),
        ("what's the next shuttle?", "Besides the Roadrunner, what's the next shuttle?"),
        ("what is for dinner today?", "If I am allergic to nuts, what is for dinner today?"),
        ("list the dinner menu today", "Besides anything with peanuts, list the dinner menu today"),
        ("what's the next shuttle", "Tell me, if it's running, what's the next shuttle"),
    ],
)
def test_a_comma_after_a_fronted_qualifier_keeps_the_part_whole(quote: str, text: str) -> None:
    # A quote starts a part after a comma only when the item before it is a request too.
    assert not independent_quote(quote, text)


def test_every_request_in_a_comma_list_is_a_part() -> None:
    text = "Tell me the next shuttle, where the Registrar is, who the dean is"
    for quote in ("Tell me the next shuttle", "where the Registrar is", "who the dean is"):
        assert independent_quote(quote, text)
    # One qualifying item breaks the chain for the requests after it.
    assert not independent_quote(
        "who the dean is", "Tell me the next shuttle, after 5 PM, who the dean is")


@pytest.mark.parametrize(
    ("quote", "text", "independent"),
    [
        ("list the dinner menu today", "list the dinner menu today", True),
        ("list the dinner menu today", "list the dinner menu today and how do I appeal?", True),
        ("the next shuttle", "What is the next shuttle?", False),
        ("the next shuttle", "Tell me the dinner menu, the next shuttle", False),
        ("what is for dinner today", "what is for dinner today if I am allergic", False),
    ],
)
def test_other_part_boundaries_are_unchanged(quote: str, text: str, independent: bool) -> None:
    assert independent_quote(quote, text) is independent
