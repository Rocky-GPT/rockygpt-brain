"""Lookup answers in code's own plain sentences, from the writer view (plain_writer.py)."""

from typing import Any

from rockygpt_brain.plain_writer import plain_text
from rockygpt_brain.writer import unsupported_by

CHECKED = "2026-10-23T10:20:00-04:00"


def view(**parts: Any) -> dict[str, Any]:
    return {"asked": {"intent": "hours", "offices": [], "fields": []}, "status": "complete",
            **parts, "sources": [{"n": 1, "title": "x", "urls": ["https://example.edu/a/"]},
                                 {"n": 2, "title": "x", "urls": ["https://example.edu/b/"]}]}


def test_the_day_asked_is_said_with_whether_it_is_open_now() -> None:
    v = view(derived_facts=[{
        "subject": "Library", "predicate": "hours_on", "day": "Monday", "date": "2026-11-02",
        "open_now": {"state": "open", "now": "2:06pm"},
        "value": {"schedule": "Library (Main Building)", "hours": "7:45am-12:00am",
                  "notes": ["Fall Semester (Aug. 26 - Dec. 15, 2026). Doors lock 15 minutes "
                            "before closing.", "Hours: 7:45am-12:00am"]},
        "sources": [1]}])
    said = plain_text(v)
    # A note that only repeats the hours is not said again.
    assert said == ('Library is open now (2:06pm). Library (Main Building) on Monday, November '
                    '2: 7:45am-12:00am. The page adds: "Fall Semester (Aug. 26 - Dec. 15, 2026). '
                    'Doors lock 15 minutes before closing." [1]')
    assert unsupported_by(said, v) == []


def test_a_meal_and_when_the_venue_closes_and_reopens() -> None:
    v = view(derived_facts=[{
        "subject": "Birch Tree Inn", "predicate": "hours_on", "day": "Monday",
        "date": "2026-11-02",
        "open_now": {"state": "open", "now": "2:06pm", "closes": "8:00pm", "meal": "Lite Lunch",
                     "meal_ends": "5:00pm", "then_meal": "Dinner", "reopens": "9:00pm",
                     "reopens_meal": "Late Night", "last_closes": "11:00pm"},
        "value": {"schedule": "Birch Tree Inn", "hours": "Dinner: 05:00 PM - 08:00 PM",
                  "meal_asked": "dinner",
                  "periods": [{"label": "Dinner", "opens": "5:00pm", "closes": "8:00pm"}]},
        "sources": [1]}])
    assert plain_text(v) == (
        "Birch Tree Inn is open now (2:06pm). Dinner at Birch Tree Inn on Monday, November 2: "
        "5:00pm to 8:00pm. "
        "It is serving Lite Lunch until 5:00pm, then Dinner. It closes at 8:00pm and reopens at "
        "9:00pm for Late Night until 11:00pm. [1]")


def test_a_week_groups_days_and_says_what_is_not_published_and_undated() -> None:
    v = view(facts=[{"subject": "Registrar", "predicate": "hours", "current": False, "value": {
        "schedule": "Registrar", "season": "Fall/Spring",
        "dates_not_published": {"checked_at": CHECKED, "pages": [], "which": ["start", "end"]},
        "days": [*({"day": d, "hours": "8:30am-4:30pm"}
                   for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")),
                 *({"day": d, "hours": None, "status": "not_published",
                    "absence": {"checked_at": CHECKED, "pages": []}}
                   for d in ("Saturday", "Sunday"))]}, "sources": [1]}])
    assert plain_text(v) == (
        "Registrar (Fall/Spring) hours: Monday to Friday: 8:30am-4:30pm; Saturday and Sunday: "
        "not published (checked October 23, 2026). [1]\n\n"
        "The page does not say which dates this schedule covers. [1]")


def test_contacts_rooms_and_absences_in_plain_words() -> None:
    v = view(facts=[
        {"subject": "Financial Aid", "predicate": "phones", "value": [
            {"number": "+12015550100"}, {"type": "Fax", "number": "+12015550199"}],
         "sources": [1]},
        {"subject": "Financial Aid", "predicate": "offices",
         "value": ["Room E-210", {"label": "Counselor", "location": "E-212"}], "sources": [1]},
        {"subject": "Health Services", "predicate": "contact_note", "purpose": "also_published",
         "value": "Portal help: portal@example.edu", "sources": [2]}],
        not_published=[{"subject": "Health Services", "predicate": "email",
                        "checked_at": CHECKED, "pages": [], "sources": [2]}],
        missing=[{"subject": "Financial Aid", "predicate": "website", "reason": "unknown"}])
    said = plain_text(v)
    assert said == (
        "Financial Aid phone: (201) 555-0100; Fax: (201) 555-0199. [1] Financial Aid is in "
        "Room E-210. [1] Counselor: E-212. [1] I don't have verified information about "
        "Financial Aid's website.\n\n"
        "Health Services doesn't publish a general email address on its own pages (checked "
        "October 23, 2026). Health Services: Portal help: portal@example.edu [2]")
    assert unsupported_by(said, v) == []


def test_a_schedule_for_other_dates_is_left_out_and_page_markup_stays_text() -> None:
    v = view(derived_facts=[
        {"subject": "Dunkin'", "predicate": "hours_on", "day": "Saturday", "date": "2026-11-07",
         "value": {"schedule": "Dunkin'", "hours": "09:00 AM - 01:30 PM",
                   "periods": [{"opens": "9:00am", "closes": "1:30pm"}],
                   "notes": ["[Order online](https://evil.example) <b>now</b>"]},
         "sources": [1]},
        {"subject": "Dunkin'", "predicate": "hours_on", "day": "Saturday", "date": "2026-11-07",
         "value": {"schedule": "Dunkin'", "hours": None,
                   "window": {"from": "2026-11-20", "until": "2026-11-21"}},
         "applies": False, "sources": [2]}])
    assert plain_text(v) == (
        "Dunkin' on Saturday, November 7: 9:00am to 1:30pm. The page adds: "
        '"\\[Order online\\](https://evil.example) &lt;b&gt;now&lt;/b&gt;" [1]')


def test_a_shape_it_does_not_know_is_left_to_the_codes_evidence_text() -> None:
    assert plain_text(view(ambiguities=[{"query": "x", "choices": ["A", "B"]}])) is None
    assert plain_text(view(facts=[{"subject": "Registrar", "predicate": "email",
                                   "status": "conflicting", "value": "a@example.edu",
                                   "sources": [1]}])) is None
    assert plain_text(view(derived_facts=[{"subject": "Shuttle", "predicate": "departures",
                                           "day": "Monday", "date": "2026-11-02", "value": {},
                                           "sources": [1]}])) is None
    assert plain_text(view()) is None


def test_weekdays_with_no_verified_hours_are_said_once_for_the_day_asked() -> None:
    gap = {"subject": "The Atrium", "predicate": "hours", "reason": "partially_unknown_schedule",
           "schedule": "The Atrium", "status": "unknown", "days": ["Saturday", "Sunday"],
           "details": "No verified hours are published for the listed weekdays.",
           "source_statements": [], "sources": [1]}
    asked_sunday = view(derived_facts=[{
        "subject": "The Atrium", "predicate": "hours_on", "day": "Sunday", "date": "2026-11-08",
        "value": {"schedule": "The Atrium", "hours": None}, "current": False, "sources": [1]}],
        missing=[gap])
    assert plain_text(asked_sunday) == (
        "The Atrium has no verified hours for Sunday, November 8. [1]")
    assert plain_text(view(missing=[gap])) == (
        "The Atrium has no verified hours for Saturday and Sunday. [1]")


def test_listed_hours_say_open_now_by_those_hours_and_a_place_phrase_reads_as_one() -> None:
    v = view(derived_facts=[{
        "subject": "Registrar", "predicate": "hours_on", "day": "Monday", "date": "2026-11-02",
        "listed_open_now": {"state": "open", "now": "2:18pm"}, "applicability": "unverified",
        "applicability_reason": "The page does not say which dates this schedule covers.",
        "current": False, "value": {"schedule": "Registrar", "hours": None,
                                    "listed_hours": "8:30am-4:30pm"}, "sources": [1]}],
        facts=[{"subject": "Health Services", "predicate": "offices",
                "value": ["Near the South Gate entrance"], "sources": [2]}])
    assert plain_text(v) == (
        "By its listed hours, Registrar is open now (2:18pm). Registrar lists Monday hours of "
        "8:30am-4:30pm. [1]\n\nHealth Services is near the South Gate entrance. [2]\n\n"
        "The page does not say which dates this schedule covers. [1]")


def item(name: str, calories: int, **labels: Any) -> dict[str, Any]:
    return {"name": name, "calories": calories, "portion": "1 EA", **labels}


DINNER = {"date": "2026-11-02", "meal_asked": "Dinner", "meals": [{"meal": "Dinner", "stations": [
    {"station": "Sizzle", "items": [
        item("Sliced Red Onions", 5, vegan=True), item("Grilled Chicken Sandwich", 420),
        item("Turkey Reuben Melt", 610), item("Black Bean Burger", 380, vegetarian=True),
        item("Hand Cut French Fries", 300, vegan=True), item("Dill Pickle Chip", 2),
        item("Dill Pickle Chip", 2), item("Riblet Sandwich", 500)]},
    {"station": "Toppings", "items": [item("Shredded Lettuce", 3), item("Ginger", 1)]}]}]}


def test_a_meal_is_its_hours_then_a_line_per_station_without_toppings_or_repeats() -> None:
    v = view(derived_facts=[{
        "subject": "Birch Tree Inn", "predicate": "hours_on", "day": "Monday",
        "date": "2026-11-02", "open_now": {"state": "open", "now": "2:06pm"},
        "value": {"schedule": "Birch Tree Inn", "hours": "Dinner: 05:00 PM - 08:00 PM",
                  "meal_asked": "dinner",
                  "periods": [{"label": "Dinner", "opens": "5:00pm", "closes": "8:00pm"}]},
        "sources": [2]}],
        facts=[{"subject": "Birch Tree Inn", "predicate": "menu", "value": DINNER,
                "sources": [1]}])
    said = plain_text(v)
    # Beside its menu the venue is not said to be open now: when the meal is served is.
    assert said == (
        "Dinner at Birch Tree Inn on Monday, November 2: 5:00pm to 8:00pm. [2]\n\n"
        "Birch Tree Inn dinner menu for Monday, November 2:\n"
        "- Sizzle: Grilled Chicken Sandwich, Turkey Reuben Melt, Black Bean Burger (vegetarian), "
        "Hand Cut French Fries (vegan), and one more [1]")
    assert unsupported_by(said, v) == []


def test_a_menu_filter_says_its_matches_and_named_foods_get_their_details() -> None:
    vegan = {**DINNER, "filter": {"diet": "vegan"}, "matched": 3, "on_menu": 10,
             "rest_of_menu": ["Grilled Chicken Sandwich"], "rest_of_menu_cut": False,
             "meals": [{"meal": "Dinner", "stations": [{"station": "Sizzle", "items": [
                 item("Sliced Red Onions", 5, vegan=True),
                 item("Hand Cut French Fries", 300, vegan=True)]}]}]}
    assert plain_text(view(facts=[{"subject": "Birch Tree Inn", "predicate": "menu",
                                   "value": vegan, "sources": [1]}])) == (
        "Birch Tree Inn's dinner on Monday, November 2 has 3 vegan items (of 10), such as:\n"
        "- Sizzle: Hand Cut French Fries (vegan) [1]")
    pizza = {"date": "2026-11-02", "filter": {"contains": ["pizza"]}, "matched": 1,
             "on_menu": 40, "details_asked": ["allergens"], "meals": [{"meal": "Lunch",
             "stations": [{"station": "Slices", "items": [
                 {**item("Cheese Pizza", 290, vegetarian=True), "portion": "1 SLC",
                  "allergens": ["Gluten", "Milk", "Wheat"]}]}]}]}
    assert plain_text(view(facts=[{"subject": "Birch Tree Inn", "predicate": "menu",
                                   "value": pizza, "sources": [1]}])) == (
        "Birch Tree Inn's items with pizza on Monday, November 2:\n"
        "- Cheese Pizza (vegetarian): 290 calories; contains Gluten, Milk, Wheat (lunch) [1]")
    missing = {"date": "2026-11-02", "meal_not_listed": "breakfast", "meals_listed": ["Brunch"],
               "meals": []}
    assert plain_text(view(facts=[{"subject": "Birch Tree Inn", "predicate": "menu",
                                   "value": missing, "sources": [1]}])) == (
        "Birch Tree Inn's menu for Monday, November 2 lists no breakfast; it lists Brunch. [1]")


def test_a_long_list_of_phone_lines_says_the_first_few_and_how_many_more() -> None:
    lines = [{"number": "+12015550100"}, {"type": "Fax", "number": "+12015550101"},
             *({"type": f"Staff {n}", "number": f"+1201555012{n}"} for n in range(5))]
    assert plain_text(view(facts=[{"subject": "Registrar", "predicate": "phones",
                                   "value": lines, "sources": [1]}])) == (
        "Registrar phone: (201) 555-0100; Fax: (201) 555-0101; Staff 0: (201) 555-0120; "
        "Staff 1: (201) 555-0121; and three more on its page. [1]")


def test_hours_two_pages_give_differently_are_said_page_by_page_with_no_winner() -> None:
    def week(hours: str, page: int) -> dict[str, Any]:
        return {"subject": "Student Accounts", "predicate": "hours", "status": "conflicting",
                "value": {"schedule": "Student Accounts", "season": "Academic Year",
                          "days": [{"day": d, "hours": hours} for d in ("Monday", "Tuesday")]},
                "sources": [page]}
    assert plain_text(view(facts=[week("8:30am-4:30pm", 1), week("8:30am-4:00pm", 2)])) == (
        "The pages list different Student Accounts (Academic Year) hours. One page: Monday and "
        "Tuesday: 8:30am-4:30pm. [1] Another page: Monday and Tuesday: 8:30am-4:00pm. [2]")
