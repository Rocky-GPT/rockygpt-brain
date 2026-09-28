"""Code writes a plain menu or hours answer only when the lookup proves every word of it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import Mock
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.campus.profile_answers import hours_answer, menu_answer
from rockygpt_brain.config import RELEASE
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.render import render_answer
from rockygpt_brain.retrieval.profiles import ProfileQuery
from test_engine import answer, review
from test_profile_sections import full_dining_data
from test_profiles import ENTITY_ID
from test_routing import answers_for

DAY = date(2026, 9, 21)  # A Monday; the dated exception ends lunch at 1 PM.
CAMPUS = ZoneInfo("America/New_York")


def birch(*, periods_published: bool = False) -> Any:
    """Example Dining's 52 lunch dishes at four stations, with its meal periods."""
    data = full_dining_data()
    enrich = data._enrich

    def with_periods(collection: str, records: list[dict[str, Any]]) -> None:
        enrich(collection, records)
        for record in records if collection == "dining_hours" else []:
            end = "01:00 PM" if record["valid_from"] else "02:00 PM"
            record["fields"]["periods"] = [
                {"label": "Lunch", "start": "11:30 AM", "end": end},
                {"label": "Dinner", "start": "05:00 PM", "end": "12:00 AM"}]
            if periods_published:
                record["coverage"]["fields"]["periods"] = "published"
    data._enrich = with_periods
    return data


def lunch(**changes: Any) -> ProfileQuery:
    return ProfileQuery.model_validate({
        "entity_id": ENTITY_ID, "include": ["menu", "hours"], "date": DAY, "meal": "lunch",
        "menu_limit": 100, **changes})


def rendered(output: dict[str, Any], result: Any) -> str:
    assert result is not None
    evidence = {record["id"]: record for record in output["records"]}
    return str(render_answer(result, evidence)["answer"])


def test_a_whole_meal_is_listed_by_station_with_its_hours() -> None:
    data = birch()
    output = data.lookup_profile(lunch())
    result = menu_answer(output, lunch(), full=True)
    assert result is not None and result.status == "answered"
    text = rendered(output, result)
    assert text.startswith(
        "Lunch at Example Dining on Monday, September 21, 11:30 AM to 1:00 PM:\n"
        "- **Published Station 0:** Dish 00, Dish 04, Dish 08")
    assert all(f"Dish {index:02}" in text for index in range(52))
    assert "That's all 52 lunch items on the published menu." in text
    # 52 dishes and the hours record: no part cites more than 50 records.
    assert [len(part.evidence_ids) for part in result.parts] == [40, 13, 0]


def test_jev_s_dishes_are_listed_and_the_rest_counted() -> None:
    data = birch()
    output = data.lookup_profile(lunch())
    dishes = {record["id"] for record in output["records"]
              if record["fields"].get("name") in {"Dish 00", "Dish 05", "Dish 10"}}
    text = rendered(output, menu_answer(output, lunch(), full=False, dishes=dishes))
    assert "- **Published Station 0:** Dish 00\n- **Published Station 1:** Dish 05" in text
    assert "Dish 01" not in text
    assert "That's 3 of the 52 lunch items. Ask for the full lunch menu to see them all." in text
    # Every item or none picked, or a failed pick, lists them all.
    for picked in (None, set(), {record["id"] for record in output["records"]}):
        assert "That's all 52" in rendered(
            output, menu_answer(output, lunch(), full=False, dishes=picked))


def test_a_diet_lists_only_labeled_dishes_and_says_so() -> None:
    data = birch()
    for record in data._fetch(None, ("dataset", "dining", ["lunch:dish-00"])):
        record["vegan"] = int(record["name"][-2:]) % 10 == 0
        record["label_coverage"] = {"vegan": "published"}
    output = data.lookup_profile(lunch(diet="vegan"))
    text = rendered(output, menu_answer(output, lunch(diet="vegan"), full=False))
    assert text.startswith("Published vegan lunch dishes at Example Dining on Monday, "
                           "September 21 (lunch is 11:30 AM to 1:00 PM):")
    assert "That's all 6 vegan lunch items" in text
    assert "a dish with no label is left out" in text


@pytest.mark.parametrize("case", ["partial list", "stale", "other day", "caveat", "no meal"])
def test_anything_the_lookup_does_not_prove_goes_to_gpt(case: str) -> None:
    data = birch()
    query = lunch()
    if case == "partial list":
        query = lunch(menu_limit=12)
    elif case == "stale":
        data.now = datetime(2026, 9, 30, 16, tzinfo=UTC)  # Past the 168-hour window.
    output = data.lookup_profile(query)
    if case == "other day":
        query = lunch(date=DAY + timedelta(days=1))
    elif case == "caveat":
        next(record for record in output["records"]
             if record["collection"] == "menu")["limitations"].append(
            "Linked records disagree on name; identity does not establish which value is "
            "authoritative.")
    elif case == "no meal":
        query = lunch(meal=None)
    assert menu_answer(output, query, full=True) is None


def test_a_day_s_hours_list_its_published_meal_periods() -> None:
    query = ProfileQuery(entity_id=UUID(ENTITY_ID), include=["hours"], date=DAY)
    output = birch().lookup_profile(query)
    assert rendered(output, hours_answer(output, query)).startswith(
        "Example Dining's published hours on Monday, September 21: 11:30 AM - 01:00 PM; "
        "05:00 PM - 12:00 AM.")
    output = birch(periods_published=True).lookup_profile(query)
    text = rendered(output, hours_answer(output, query))
    assert text.startswith("Example Dining on Monday, September 21: Lunch 11:30 AM to 1:00 PM; "
                           "Dinner 5:00 PM to 12:00 AM.")
    # The dated exception applies, so no "regular schedule" caveat.
    assert "regular published schedule" not in text
    meal = query.model_copy(update={"meal": "Lunch"})
    output = birch().lookup_profile(meal)
    assert rendered(output, hours_answer(output, meal)).startswith(
        "Lunch at Example Dining on Monday, September 21 is 11:30 AM to 1:00 PM.")


def test_a_note_on_the_hours_page_is_stated_word_for_word() -> None:
    query = ProfileQuery(entity_id=UUID(ENTITY_ID), include=["hours"], date=DAY)
    output = birch().lookup_profile(query)
    for record in output["records"]:
        record["fields"]["notes"] = ("Fall Semester (Aug. 26 - Dec. 15, 2026). Please note that "
                                     "the front doors are locked 15 minutes before closing.")
    assert rendered(output, hours_answer(output, query)).startswith(
        "Example Dining's published hours on Monday, September 21: 11:30 AM - 01:00 PM; "
        "05:00 PM - 12:00 AM. The hours page adds: Fall Semester (Aug. 26 - Dec. 15, 2026). "
        "Please note that the front doors are locked 15 minutes before closing.")


def test_hours_code_cannot_verify_go_to_gpt() -> None:
    query = ProfileQuery(entity_id=UUID(ENTITY_ID), include=["hours"], date=DAY)
    data = birch()
    for record in data._fetch(None, ("dataset", "dining", ["Monday"])):
        record["schedule"] = "Hours unavailable"
    output = data.lookup_profile(query)
    assert hours_answer(output, query) is None
    # A meal the schedule doesn't label.
    brunch = query.model_copy(update={"meal": "Brunch"})
    assert hours_answer(birch().lookup_profile(brunch), brunch) is None


def dining_router(**values: Any) -> Mock:
    choices = {"route": "profile", "entity": ENTITY_ID, "date": "named", "meal": "lunch",
               "detail_menu": 0.99, "detail_contact": 0.0, **values}
    router = Mock()
    router.route.side_effect = lambda payload, **kwargs: answers_for(payload, **choices)
    # Jev says every fourth item is a dish.
    router.filter.side_effect = lambda payload, **kwargs: {
        key: {"type": "noul", "noul": 0.97 if int(key.split("_")[1]) % 4 == 0 else 0.02}
        for key in payload["questions"]}
    return router


def ask(text: str, router: Mock, gpt: Mock) -> dict[str, Any]:
    data = birch()
    data.now = datetime(2026, 9, 21, 16, tzinfo=UTC)
    return run_turn([ChatMessage(role="user", content=text)], client=gpt, data=data,
                    model=RELEASE.model, now=datetime(2026, 9, 21, 12, tzinfo=CAMPUS),
                    routing_client=router, routing_mode="active")


def test_a_plain_menu_request_is_answered_without_gpt() -> None:
    gpt, router = Mock(), dining_router()
    result = ask("What's for lunch at Example Dining today?", router, gpt)
    gpt.create.assert_not_called()
    assert result["metrics"]["responseMode"] == "exact_menu"
    assert result["metrics"]["dishPick"]["items"] == 52
    assert result["metrics"]["dishPick"]["dishes"] == 13
    assert "That's 13 of the 52 lunch items." in result["answer"]
    # Every dish that matched was fetched: nothing was cut before Jev picked.
    assert result["trace"][0]["arguments"]["menu_limit"] == 100
    # "The full lunch menu" lists every item and needs no pick.
    router = dining_router(complete_menu=0.98)
    result = ask("What's the full lunch menu at Example Dining today?", router, gpt)
    router.filter.assert_not_called()
    assert "That's all 52 lunch items" in result["answer"]


def test_a_failed_dish_pick_still_lists_every_item() -> None:
    gpt, router = Mock(), dining_router()
    router.filter.side_effect = TimeoutError("late")
    result = ask("What's for lunch at Example Dining today?", router, gpt)
    gpt.create.assert_not_called()
    assert result["metrics"]["dishPick"]["reason"] == "timeout"
    assert "That's all 52 lunch items" in result["answer"]


def test_a_menu_request_with_a_condition_is_gpt_s() -> None:
    gpt = Mock()
    gpt.create.side_effect = [answer("Dish 00 is served."), review()]
    result = ask("What's good for lunch at Example Dining today?",
                 dining_router(menu_condition=0.96), gpt)
    assert gpt.create.call_count == 2
    assert result["metrics"]["responseMode"] == "reviewed_prose"


def decide(text: str, **values: Any) -> Any:
    from rockygpt_brain.core.routing import interpret, routing_payload, validate_answers
    from rockygpt_brain.retrieval.profiles import Identity

    venue = Identity.model_validate(birch()._artifacts["campus-identities"]["entities"][0])
    request = [ChatMessage(role="user", content=text)]
    now = datetime(2026, 9, 21, 12, tzinfo=CAMPUS)
    payload, day = routing_payload(request, [venue], now)
    choices = {"route": "profile", "entity": ENTITY_ID, "date": "named", "meal": "lunch",
               "detail_menu": 0.99, "detail_contact": 0.0, **values}
    answers = answers_for(payload, **choices)
    validate_answers(answers, payload["questions"])
    return interpret(answers, [venue], day, request)


@pytest.mark.parametrize(
    "values,template,limit,diet",
    [
        ({}, "menu", 100, None),
        ({"complete_menu": 0.98}, "full_menu", 100, None),
        # Hours asked with the menu: code adds the meal's hours.
        ({"detail_hours": 0.97}, "menu", 100, None),
        ({"diet": "vegan"}, "menu", 100, "vegan"),
        # A whole day's menu keeps the dozen, across stations, and GPT writes it.
        ({"meal": "none"}, None, 12, None),
        ({"menu_condition": 0.96}, None, 100, None),
        ({"menu_condition": 0.3}, None, 100, None),
        ({"diet": "other"}, None, 100, None),
        ({"detail_location": 0.5}, None, 100, None),
        ({"detail_menu": 0.02, "detail_hours": 0.99, "meal": "none"}, "hours", None, None),
        ({"detail_menu": 0.02, "detail_hours": 0.99, "at_time": 0.93}, None, None, None),
        ({"detail_menu": 0.02, "detail_hours": 0.99, "detail_location": 0.4}, None, None,
         None),
    ],
    ids=["plain", "full", "with hours", "vegan", "no meal", "condition", "unsure condition",
         "other diet", "unsure other detail", "hours", "hours now", "hours and location"],
)
def test_jev_decides_what_code_writes_and_how_much_is_fetched(
    values: dict[str, Any], template: str | None, limit: int | None, diet: str | None,
) -> None:
    decision = decide("What's for lunch at Example Dining today?", **values)
    assert decision.template == template
    assert decision.arguments is not None
    assert decision.arguments.get("menu_limit") == (limit if limit else 12)
    assert decision.arguments.get("diet") == diet
