"""The Atrium's menu board: items read from the menu file it posts, answered by code.

The board is not dated and names no lunch or dinner. Its items are graph records the venue links;
code lists them, finds the ones a student names, and says their prices, calories and the allergens
the board lists, in its own words with no model call for a plain question."""

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.retrieval.entity_facts import menu_board_property
from rockygpt_brain.router import code_route
from rockygpt_brain.turn import intake
from test_engine import ScriptedGateway, completion, finish

EASTERN = ZoneInfo("America/New_York")
TUESDAY_10AM = datetime(2026, 11, 3, 10, 0, tzinfo=EASTERN)
COLLECTED = datetime(2026, 11, 3, 6, 0, tzinfo=EASTERN)
PDF = "https://media.example/web/en-us/media/Atrium%20Menu%20Boards%202026.pdf"
TITLE = "Atrium Menu Boards 2026"

ITEMS: list[dict[str, Any]] = [
    {"page": 1, "station": "The Grill", "section": "Breakfast", "name": "Steak & Eggs",
     "description": "Eggs, Steak, Caramelized Onions, Cheddar Cheese and Rocky road Sauce in a "
                    "Kaiser Roll", "prices": [{"price": "$9.99", "calories": "860 cal"}],
     "contains": "Milk, Eggs, Wheat, Soy and Gluten"},
    {"page": 1, "station": "The Grill", "section": "Breakfast", "name": "Crunch Wrap",
     "prices": [{"price": "$8.99", "calories": "660 cal"}],
     "contains": "Milk, Eggs, Wheat, Soy and Gluten"},
    {"page": 1, "station": "The Grill", "section": "Breakfast sides", "name": "Hash Brown",
     "prices": [{"price": "$1.99", "calories": "150 cal"}]},
    {"page": 2, "station": "The Grill", "section": "Burgers and Sandwiches", "name": "Hamburger",
     "description": "brioche bun", "prices": [{"price": "$8.99", "calories": "674 cal"}]},
    {"page": 2, "station": "The Grill", "section": "Burgers and Sandwiches",
     "name": "Crispy Chicken Sandwich", "prices": [{"price": "$9.99", "calories": "460 cal"}]},
    {"page": 2, "station": "The Grill", "section": "Burgers and Sandwiches", "name": "BLT",
     "prices": [{"price": "$7.99", "calories": "665 cal"}]},
    {"page": 2, "station": "The Grill", "section": "Burgers and Sandwiches",
     "name": "Grilled Cheese", "prices": [{"price": "$7.99", "calories": "565 cal"}]},
    {"page": 2, "station": "The Grill", "section": "Burgers and Sandwiches",
     "name": "Turkey Burger", "prices": [{"price": "$8.99", "calories": "435 cal"}]},
    {"page": 5, "station": "Sandwiches", "section": "Specialty Sandwiches", "name": "Chicken Parm",
     "description": "Crispy Chicken, Provolone Cheese and Marinara Sauce",
     "prices": [{"size": "6\"", "price": "$9.99", "calories": "540 cal"},
                {"size": "12\"", "price": "$11.99", "calories": "1080 cal"}],
     "contains": "Milk, Eggs, Wheat, Soy and Gluten"},
    {"page": 5, "station": "Sandwiches", "section": "Specialty Sandwiches",
     "name": "Chickpea Salad", "prices": [{"size": "6\"", "price": "$9.49"}],
     "contains": "Wheat, Soy, Gluten and Gluten"},
    {"page": 6, "station": "Taqueria", "section": "Build Your Own Entree",
     "name": "Build Your Own Entree",
     "options": [{"group": "Style", "name": "Burrito", "calories": "203 cal"},
                 {"group": "Style", "name": "Three Tacos", "calories": "220 cal"},
                 {"group": "Protein", "name": "Pollo Asado", "calories": "204 cal",
                  "price": "$10.49"}]},
    {"page": 7, "station": None, "section": "Sides", "name": "Brown Rice",
     "prices": [{"price": "$3.99", "calories": "230 cal"}],
     "note": "Marked vegan (VG) on the board."},
    {"page": 7, "station": None, "section": "Sides", "name": "Lo Mein",
     "prices": [{"price": "$4.49", "calories": "270 cal"}],
     "note": "Marked vegetarian (V) on the board."},
    {"page": 5, "station": "Sandwiches", "section": "Specialty Sandwiches",
     "name": "Roadrunner BTL", "prices": [{"size": "6\"", "price": "$9.99"}],
     "note": "The board prints the 12\" price as a misprint, so it is left out."},
]


def _key(item: dict[str, Any]) -> str:
    return f"{item['page']}:{item['station'] or '-'}:{item['section']}:{item['name']}"


def board_rows() -> list[dict[str, Any]]:
    return [{"id": f"menu_board_items:{_key(item)}", "source_key": "dining",
             "source_record_key": _key(item), "collected_at": COLLECTED, "content_hash": "c" * 64,
             "venue": "The Atrium", **item, "document_title": TITLE, "read_at": "2026-11-02",
             "source_url": PDF} for item in ITEMS]


def document_rows() -> list[dict[str, Any]]:
    return [{"id": "document-1", "source_key": "dining",
             "source_record_key": "The Atrium:menu-document:Atrium Menu Boards 2026",
             "canonical_url": "https://example.edu/dining", "freshness_sla_hours": 36,
             "collected_at": COLLECTED, "content_hash": "d" * 64, "name": "The Atrium",
             "title": TITLE, "url": PDF, "mime_type": "application/pdf"}]


def atrium() -> MemoryEntityFacts:
    rows, documents = board_rows(), document_rows()
    entities = [{"id": "atrium", "name": "The Atrium", "kind": "venue", "aliases": [], "links": [
        {"collection": "menu_documents", "source_key": "dining",
         "source_record_keys": [documents[0]["source_record_key"]]},
        {"collection": "menu_board_items", "source_key": "dining",
         "source_record_keys": [row["source_record_key"] for row in rows]}]}]
    return MemoryEntityFacts(dataset_version="r", identity_hash="i", entities=entities,
                             contacts=[], records={"menu_documents": documents,
                                                   "menu_board_items": rows},
                             now=lambda: TUESDAY_10AM)


def board(meal: str | None = None, **query: Any) -> dict[str, Any]:
    found = menu_board_property(board_rows(), document_rows(), TUESDAY_10AM, meal, query or None)
    assert found is not None
    value: dict[str, Any] = found[0]["values"][0]["value"]
    return value


def ask(message: str, *calls: Any) -> Any:
    gateway = ScriptedGateway(*calls)
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": message}]})
    engine = ChatEngine(gateway, atrium(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=TUESDAY_10AM), request))
    return result, gateway


def menu_lookup(**request: Any) -> Any:
    return completion("graph_lookup", {"requests": [
        {"query": "The Atrium", "fields": ["menu"], **request}]})


def test_the_atrium_publishes_a_menu_board_not_a_dated_menu() -> None:
    listing = atrium().list_offices(limit=10, kinds=("venue",))
    assert listing["offices"][0]["publishes"] == ["menu board"]


def test_a_plain_atrium_menu_question_is_answered_by_code_with_no_model_call() -> None:
    result, gateway = ask("What's on the Atrium menu?")
    assert gateway.inputs == []
    assert result.trace["writer"] == {"used": True, "by": "code"}
    answer = result.body["answer"]
    assert answer.startswith("The Atrium's menu board lists 14 items. Some of each section, "
                             "with prices:\n- The Grill, Breakfast: Steak & Eggs ($9.99), "
                             "Crunch Wrap ($8.99)\n")
    assert ("- The Grill, Burgers and Sandwiches: Hamburger ($8.99), Crispy Chicken Sandwich "
            "($9.99), BLT ($7.99), Grilled Cheese ($7.99), and one more") in answer
    assert "- Sandwiches, Specialty Sandwiches: Chicken Parm ($9.99 / $11.99)," in answer
    assert "- Sides: Brown Rice ($3.99, vegan), Lo Mein ($4.49, vegetarian)" in answer
    assert "- Taqueria, Build Your Own Entree: Build Your Own Entree" in answer
    assert f"Sources: [1]({PDF})" in answer
    assert "misprint" not in answer  # A reviewer's note is never said.


def test_breakfast_is_the_boards_breakfast_sections_in_full() -> None:
    value = board("breakfast")
    assert value["meal_asked"] == "breakfast" and value["detailed"] is True
    assert [s["section"] for s in value["sections"]] == ["Breakfast", "Breakfast sides"]
    result, _ = ask("What's for breakfast at the Atrium?")
    answer = result.body["answer"]
    assert "The Atrium's menu board, breakfast items:" in answer
    assert ("- Steak & Eggs (The Grill, Breakfast): $9.99 860 cal. Eggs, Steak, Caramelized "
            "Onions, Cheddar Cheese and Rocky road Sauce in a Kaiser Roll. Contains Milk, Eggs, "
            "Wheat, Soy and Gluten.") in answer
    assert "- Hash Brown (The Grill, Breakfast sides): $1.99 150 cal." in answer


def test_a_meal_the_board_does_not_name_says_so_and_lists_the_board() -> None:
    value = board("dinner")
    assert value["meal_not_on_board"] == "dinner" and len(value["sections"]) == 6
    result, _ = ask("Atrium dinner menu")
    assert result.body["answer"].startswith(
        "The Atrium's menu board doesn't list a separate dinner menu.")


def test_a_named_food_gets_its_prices_calories_and_allergens() -> None:
    result, _ = ask("how much is the chicken parm at the atrium",
                    menu_lookup(items=["chicken parm"]), finish())
    answer = result.body["answer"]
    assert ("The Atrium's menu board items with chicken parm:\n- Chicken Parm (Sandwiches, "
            "Specialty Sandwiches): 6\" $9.99 540 cal; 12\" $11.99 1080 cal. Crispy Chicken, "
            "Provolone Cheese and Marinara Sauce. Contains Milk, Eggs, Wheat, Soy and "
            "Gluten.") in answer
    assert result.trace["writer"] == {"used": True, "by": "code"}


def test_a_food_that_is_an_option_names_the_item_it_is_part_of() -> None:
    value = board(contains=["burrito"])
    (section,) = value["sections"]
    assert section["items"][0]["name"] == "Build Your Own Entree"
    assert section["items"][0]["options_matched"] == ["Burrito"]
    result, _ = ask("do they have burritos at the atrium", menu_lookup(items=["burrito"]),
                    finish())
    assert ("- Build Your Own Entree (Taqueria, Build Your Own Entree). Style: Burrito "
            "(203 cal).") in result.body["answer"]


def test_nothing_matching_names_the_boards_stations() -> None:
    value = board(contains=["sushi"])
    assert value["matched"] == 0 and value["sections"] == []
    assert value["stations_listed"] == ["The Grill", "Sandwiches", "Taqueria"]
    result, _ = ask("sushi at the atrium?", menu_lookup(items=["sushi"]), finish())
    assert ("Nothing on The Atrium's menu board matches sushi; its stations are The Grill, "
            "Sandwiches, Taqueria.") in result.body["answer"]


def test_vegan_items_are_the_ones_the_board_marks() -> None:
    assert [i["name"] for s in board(diet="vegan")["sections"] for i in s["items"]] == [
        "Brown Rice"]
    vegetarian = board(diet="vegetarian")
    assert [i["name"] for s in vegetarian["sections"] for i in s["items"]] == [
        "Brown Rice", "Lo Mein"]
    result, _ = ask("vegetarian food at the atrium", menu_lookup(diet="vegetarian"), finish())
    answer = result.body["answer"]
    assert "The Atrium's menu board marks two items vegan or vegetarian:" in answer
    assert "- Lo Mein (Sides): $4.49 270 cal. The board marks it vegetarian." in answer


def test_allergen_filters_use_only_items_that_list_their_allergens() -> None:
    free = board(without_allergens=["dairy"])
    assert [i["name"] for s in free["sections"] for i in s["items"]] == ["Chickpea Salad"]
    assert free["allergen_words"] == {"dairy": ["Milk"]}
    assert free["allergens_listed"] == 4 and free["on_board"] == 14
    result, _ = ask("anything dairy free at the atrium",
                    menu_lookup(without_allergens=["dairy"]), finish())
    answer = result.body["answer"]
    assert "Chickpea Salad (Sandwiches, Specialty Sandwiches)" in answer
    assert ("The board lists allergens for only 4 of its 14 items, so the others can't be "
            "checked.") in answer


def test_an_allergen_no_item_lists_lists_nothing_and_says_what_the_board_lists() -> None:
    value = board(with_allergens=["tree nuts"])
    assert value["allergens_unlabelled"] == ["tree nuts"] and value["sections"] == []
    result, _ = ask("what at the atrium has tree nuts", menu_lookup(with_allergens=["tree nuts"]),
                    finish())
    answer = result.body["answer"]
    assert ("The Atrium's menu board lists allergens for four of its 14 items: Milk, Eggs, "
            "Wheat, Soy, Gluten.") in answer
    assert "cannot confirm that an item is free of tree nuts" in answer


def test_a_protein_question_says_the_board_lists_only_price_and_calories() -> None:
    result, _ = ask("high protein food at the atrium", menu_lookup(items=["high protein"]),
                    finish())
    assert ("The menu board lists each item's price and calories; it does not list "
            "high protein.") in result.body["answer"]


def test_router_sends_food_and_price_words_about_a_venue_to_its_menu() -> None:
    venues = [{"name": "The Atrium", "publishes": ["menu board"]}]
    assert code_route("food at the Atrium", None, venues) == {
        "requests": [{"query": "The Atrium", "fields": ["menu"]}]}
    assert code_route("The Atrium prices", None, venues) == {
        "requests": [{"query": "The Atrium", "fields": ["menu"]}]}
    assert code_route("atrium menu", None, venues) == {  # "the" is no part of its name.
        "requests": [{"query": "The Atrium", "fields": ["menu"]}]}
    # A meal asked of no venue is the dated menus' only: the board is never today's lunch.
    assert code_route("What's for lunch today?", None, venues) is None


def test_a_board_from_another_file_or_with_no_title_is_not_read() -> None:
    rows = board_rows()
    rows[0] = {**rows[0], "source_url": "https://media.example/other.pdf"}
    assert menu_board_property(rows, document_rows(), TUESDAY_10AM) is None


def test_nothing_free_of_an_allergen_says_how_many_items_list_allergens_at_all() -> None:
    result, _ = ask("anything gluten free at the atrium",
                    menu_lookup(without_allergens=["gluten"]), finish())
    assert ("None of the 4 items on The Atrium's menu board that list allergens is free of "
            "gluten. The other 10 items list no allergens, so they can't be checked."
            ) in result.body["answer"]


def test_a_plain_spelling_finds_an_accented_name() -> None:
    rows = [{**row, "name": "Açaí Bowl"} if row["name"] == "Brown Rice" else row
            for row in board_rows()]
    found = menu_board_property(rows, document_rows(), TUESDAY_10AM, None,
                                {"contains": ["acai bowl"]})
    assert found is not None
    value = found[0]["values"][0]["value"]
    assert [i["name"] for s in value["sections"] for i in s["items"]] == ["Açaí Bowl"]
