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
                                   "status": "multiple", "value": "a@example.edu",
                                   "sources": [1]}])) is None
    assert plain_text(view(facts=[{"subject": "Registrar", "predicate": "procedures",
                                   "status": "conflicting", "value": {"text": "x"},
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


# Card shapes the check over every office and venue in the 11-03 release graph found the plain
# writer could not say (scripts/check_plain_writer.py): each went to the student as code's
# evidence text, with notes like "captured 2026-10-23T14:20".

def test_a_seasonal_week_is_said_and_its_gap_is_not_said_twice() -> None:
    # The gym's Fall hours: its gap names the schedule's season, and the week already says
    # Saturday is not listed.
    v = view(facts=[{"subject": "Auxiliary Gym", "predicate": "hours", "current": False,
                     "value": {"schedule": "Auxiliary Gym", "season": "Fall Semester Hours",
                               "days": [{"day": "Friday", "hours": "8:00am-9:30am"},
                                        {"day": "Saturday", "hours": None},
                                        {"day": "Sunday", "hours": "12:00pm-4:00pm"}]},
                     "sources": [1]}],
             missing=[{"subject": "Auxiliary Gym", "predicate": "hours",
                       "reason": "partially_unknown_schedule", "schedule": "Auxiliary Gym",
                       "status": "unknown", "season": "Fall Semester Hours",
                       "details": "No verified hours are published for the listed weekdays.",
                       "days": ["Saturday"], "source_statements": [], "sources": [1]}])
    said = plain_text(v)
    assert said == (
        "Auxiliary Gym (Fall Semester Hours) hours: Friday: 8:00am-9:30am; Saturday: not listed; "
        "Sunday: 12:00pm-4:00pm. [1]\n\n"
        "The page does not say which dates this schedule covers. [1]")
    assert unsupported_by(said, v) == []


def test_hours_a_schedule_gives_without_days_are_said_in_its_own_words() -> None:
    # Purchasing's page gives "Normal: 8:30 a.m. to 4:30 p.m." and names no days: no day is
    # assumed, and the hours are not hidden either.
    v = view(missing=[{"subject": "Purchasing", "predicate": "hours",
                       "reason": "partially_unknown_schedule", "schedule": "Purchasing",
                       "status": "unknown", "season": "Normal",
                       "details": "Normal: 8:30 a.m. to 4:30 p.m.",
                       "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                                "Saturday", "Sunday"],
                       "source_statements": [],
                       "validity_absence": {"valid_from": {"status": "not_published"}},
                       "sources": [1]}])
    said = plain_text(v)
    assert said == ('Purchasing (Normal) has no verified hours for any day. Its listing adds: '
                    '"Normal: 8:30 a.m. to 4:30 p.m." [1]')
    assert unsupported_by(said, v) == []


def test_two_listings_of_one_days_hours_are_both_said_with_whether_it_is_open_now() -> None:
    # The IT Help Desk's page lists walk-in and support hours: neither is picked.
    def listing(hours: str, note: str) -> dict[str, Any]:
        return {"subject": "IT Help Desk", "predicate": "hours_on", "status": "conflicting",
                "day": "Tuesday", "date": "2026-11-03", "current": False,
                "listed_open_now": {"state": "closed", "now": "1:18am"},
                "applicability": "unverified",
                "applicability_reason": "The page does not say which dates this schedule covers.",
                "value": {"schedule": "IT Help Desk", "season": "Fall/Spring", "hours": None,
                          "listed_hours": hours, "notes": [note]}, "sources": [1]}
    v = view(derived_facts=[listing("8:00am-8:00pm", "Walk-in hours. 4th floor."),
                            listing("8:30am-8:00pm", "Friday 8:30 AM - 6:00 PM. Support.")])
    said = plain_text(v)
    assert said == (
        "By its listed hours, IT Help Desk is closed now (1:18am). The page lists different IT "
        "Help Desk (Fall/Spring) hours for Tuesday, November 3. One listing: 8:00am-8:00pm. The "
        'page adds: "Walk-in hours. 4th floor." Another listing: 8:30am-8:00pm. The page adds: '
        '"Friday 8:30 AM - 6:00 PM. Support." [1]\n\n'
        "The page does not say which dates this schedule covers. [1]")
    assert unsupported_by(said, v) == []
    # From two pages, each is said with its own page's mark; a different schedule is named.
    other = {**listing("8:30am-4:30pm", "x"), "sources": [2], "listed_open_now": {
        "state": "open", "now": "1:18am"}, "value": {"schedule": "IT Help Desk (Summer)",
                                                     "hours": None,
                                                     "listed_hours": "8:30am-4:30pm"}}
    assert plain_text(view(derived_facts=[listing("8:00am-8:00pm", "Walk-in hours."),
                                          other])) == (
        "The pages list different IT Help Desk hours for Tuesday, November 3. One page, for IT "
        'Help Desk (Fall/Spring): 8:00am-8:00pm. [1] The page adds: "Walk-in hours." [1] Another '
        "page, for IT Help Desk (Summer): 8:30am-4:30pm. [2]\n\n"
        "The page does not say which dates this schedule covers. [1][2]")


def test_rooms_a_page_gives_differently_are_both_said_with_no_winner() -> None:
    v = view(facts=[
        {"subject": "Degree Completion", "predicate": "email", "value": "dc@example.edu",
         "sources": [1]},
        {"subject": "Degree Completion", "predicate": "offices", "status": "conflicting",
         "value": [{"label": "Director", "location": "ASB-435"}], "sources": [1]},
        {"subject": "Degree Completion", "predicate": "offices", "status": "conflicting",
         "value": ["ASB-424"], "sources": [1]}])
    said = plain_text(v)
    assert said == (
        "Degree Completion email: dc@example.edu. The page lists more than one room for Degree "
        "Completion. Degree Completion is in ASB-435 (Director). Degree Completion is in ASB-424. "
        "[1]")
    assert unsupported_by(said, v) == []


def test_special_hours_for_published_dates_are_said_with_those_dates() -> None:
    # Dunkin's Saturday hours for two weekends: a venue's dated rows are exceptions to its week.
    def weekend(sources: list[int]) -> dict[str, Any]:
        return {"subject": "Dunkin'", "predicate": "hours", "current": False, "value": {
            "schedule": "Dunkin'", "days": [{"day": "Friday", "hours": None},
                                            {"day": "Saturday", "hours": "09:00 AM - 01:30 PM"}],
            "notes": []}, "sources": sources}
    dated = {**view(), "asked": {"intent": "hours", "offices": ["Dunkin'"], "fields": ["hours"],
                                 "asOf": "2026-11-03T01:21:16-05:00"},
             "facts": [{"subject": "Dunkin'", "predicate": "hours", "value": {
                 "schedule": "Dunkin'", "days": [{"day": "Saturday", "hours": "Closed"}]},
                 "sources": [1]}, weekend([2, 3])],
             "missing": [{"subject": "Dunkin'", "predicate": "hours",
                          "reason": "partially_unknown_schedule", "schedule": "Dunkin'",
                          "status": "unknown", "details": "No verified hours are published.",
                          "days": ["Friday"], "source_statements": [], "sources": [n]}
                         for n in (2, 3)],
             "sources": [{"n": 1, "title": "dining", "urls": ["https://example.edu/d/"]},
                         {"n": 2, "title": "dining", "urls": ["https://example.edu/d/"],
                          "validity": "future", "valid_from": "2026-11-06",
                          "valid_until": "2026-11-07"},
                         {"n": 3, "title": "dining", "urls": ["https://example.edu/d/"],
                          "validity": "future", "valid_from": "2027-01-08",
                          "valid_until": "2027-01-08"}]}
    said = plain_text(dated)
    assert said == (
        "Dunkin' hours: Saturday: Closed. [1] Dunkin' hours for November 6 to November 7 and "
        "January 8, 2027: Friday: not listed; Saturday: 09:00 AM - 01:30 PM. [2][3]")
    assert unsupported_by(said, dated) == []
    # A schedule that is not current for a reason the view does not give is not guessed at.
    assert plain_text(view(facts=[weekend([1])])) is None


def test_rooms_that_are_all_labelled_still_say_whose_rooms_they_are() -> None:
    # 11-03 practice: "Main Office: C-209; ASB Office: ASB-513." never named the Career Center.
    v = view(facts=[{"subject": "Career Center", "predicate": "offices", "value": [
        {"label": "Main Office", "location": "C-209"},
        {"label": "ASB Office", "location": "ASB-513"}], "sources": [1]}])
    said = plain_text(v)
    assert said == "Career Center is in C-209 (Main Office); ASB-513 (ASB Office). [1]"
    assert unsupported_by(said, v) == []


def test_a_meal_asked_of_a_venue_says_its_hours_whether_or_not_it_names_its_meals() -> None:
    # 11-03 practice: lunch at a venue that posts one set of hours with no meal names was left
    # to the evidence text. Its hours are said as they are, and no meal is said served or not.
    unnamed = view(derived_facts=[{
        "subject": "The Atrium", "predicate": "hours_on", "day": "Tuesday", "date": "2026-11-03",
        "value": {"schedule": "The Atrium", "hours": "7:30am - 7:00pm", "meal_asked": "lunch",
                  "meals_not_labelled": True,
                  "periods": [{"opens": "7:30am", "closes": "7:00pm"}]},
        "sources": [1]}])
    said = plain_text(unnamed)
    assert said == "The Atrium on Tuesday, November 3: 7:30am to 7:00pm. [1]"
    assert unsupported_by(said, unnamed) == []
    # Named meals that leave out the one asked: it is not listed, and the day's meals are said.
    other = view(derived_facts=[{
        "subject": "Birch Tree Inn", "predicate": "hours_on", "day": "Saturday",
        "date": "2026-11-07",
        "value": {"schedule": "Birch Tree Inn", "meal_asked": "Lunch", "meal_not_served": True,
                  "hours": "Brunch: 10:00 AM - 02:00 PM; Dinner: 05:00 PM - 08:00 PM",
                  "periods": [{"label": "Brunch", "opens": "10:00am", "closes": "2:00pm"},
                              {"label": "Dinner", "opens": "5:00pm", "closes": "8:00pm"}]},
        "sources": [1]}])
    said = plain_text(other)
    assert said == (
        "Birch Tree Inn doesn't list lunch on Saturday, November 7. Brunch at Birch Tree Inn on "
        "Saturday, November 7: 10:00am to 2:00pm. Dinner at Birch Tree Inn on Saturday, "
        "November 7: 5:00pm to 8:00pm. [1]")
    assert unsupported_by(said, other) == []


def building(names: list[str]) -> list[dict[str, Any]]:
    subject = "Student Center (SC)"
    return [{"subject": subject, "predicate": "map", "value": "https://map.example.edu/sc",
             "sources": [1]},
            {"subject": subject, "predicate": "room_prefixes", "value": ["SC", "SCA"],
             "sources": [1]},
            {"subject": subject, "predicate": "places_in", "sources": [1, 2],
             "value": [{"name": name, "kind": "office"} for name in names]}]


def test_a_building_is_where_the_map_shows_it_its_room_numbers_and_the_places_in_it() -> None:
    names = [f"Office {letter}" for letter in "ABCDEFGHIJ"]
    v = view(asked={"intent": "location", "offices": [], "fields": ["offices"]},
             facts=building(names))
    said = plain_text(v)
    assert said == (
        "Student Center (SC) is on the campus map: https://map.example.edu/sc. [1] Room numbers "
        "in Student Center (SC) start with SC or SCA. [1] Places in Student Center (SC) include "
        "Office A, Office B, Office C, Office D, Office E, Office F, Office G, Office H and two "
        "more. [1][2]")
    assert said is not None and unsupported_by(said, v) == []


def test_asked_what_is_in_a_building_every_place_is_named() -> None:
    names = [f"Office {letter}" for letter in "ABCDEFGHIJ"]
    v = view(asked={"intent": "office_facts", "offices": [], "fields": ["offices", "places_in"]},
             facts=building(names))
    said = plain_text(v)
    assert said is not None and said.endswith(
        "Places in Student Center (SC) include Office A, Office B, Office C, Office D, Office E, "
        "Office F, Office G, Office H, Office I and Office J. [1][2]")
    assert unsupported_by(said, v) == []


def test_a_place_with_no_room_of_its_own_is_in_the_building_the_map_puts_it_in() -> None:
    def placed(*buildings: str) -> dict[str, Any]:
        return view(asked={"intent": "contact", "offices": [], "fields": ["offices"]}, facts=[
            {"subject": "Marketing", "predicate": "building", "sources": [1, 2],
             "value": [{"name": name, "map": f"https://map.example.edu/{name[-1].lower()}"}
                       for name in buildings]}])
    one = placed("Academic Building D")
    said = plain_text(one)
    assert said == ("Marketing is in Academic Building D, which is on the campus map: "
                    "https://map.example.edu/d. [1][2]")
    assert said is not None and unsupported_by(said, one) == []
    two = placed("Academic Building A", "Academic Building D")
    said = plain_text(two)
    assert said == (
        "Marketing is in Academic Building A and Academic Building D. Academic Building A is on "
        "the campus map: https://map.example.edu/a. Academic Building D is on the campus map: "
        "https://map.example.edu/d. [1][2]")
    assert said is not None and unsupported_by(said, two) == []


def test_a_buildings_hours_are_the_hours_its_places_publish() -> None:
    hours = {"subject": "Office A", "predicate": "hours_on", "day": "Sunday",
             "date": "2026-11-08",
             "value": {"schedule": "Office A", "hours": "12:00pm-12:00am"}, "sources": [3]}
    v = view(asked={"intent": "hours", "offices": [], "fields": ["hours"]},
             facts=building(["Office A", "Office B"]), derived_facts=[hours])
    v["sources"].append({"n": 3, "title": "x", "urls": ["https://example.edu/c/"]})
    # Asked its hours, a building says it has none of its own, marked by nothing: each place's
    # hours carry their own marks (11-03 practice: the line carried every place's five marks).
    # The map is not what was asked.
    assert plain_text(v) == (
        "I don't have hours for Student Center (SC) as a whole, but places in it list their "
        "own.\n\nOffice A on Sunday, November 8: 12:00pm-12:00am. [3]")
    alone = view(asked={"intent": "hours", "offices": [], "fields": ["hours"]},
                 facts=building(["Office A"]))
    assert plain_text(alone) == "I don't have hours for Student Center (SC)."


def test_a_room_that_is_a_floor_is_on_it_and_a_halls_office_is_the_halls() -> None:
    hall = view(asked={"intent": "location", "offices": [], "fields": ["offices"]}, facts=[
        {"subject": "Pine Hall", "predicate": "map", "value": "https://map.example.edu/ph",
         "sources": [1]},
        {"subject": "Pine Hall", "predicate": "offices", "value": ["First floor"],
         "sources": [2]}])
    said = plain_text(hall)
    # The residence hall's office is named like its building, so the floor is its office's.
    assert said == ("Pine Hall is on the campus map: https://map.example.edu/ph. [1] Pine Hall's "
                    "office is on the first floor. [2]")
    assert said is not None and unsupported_by(said, hall) == []
    office = view(facts=[{"subject": "Bursar", "predicate": "offices", "value": ["2nd Floor"],
                          "sources": [1]}])
    assert plain_text(office) == "Bursar is on the 2nd Floor. [1]"


def section(subject: str, heading: str, sentences: int, source: int) -> dict[str, Any]:
    text = " ".join(f"{subject} {heading} rule {n} keeps the shared space respectful for "
                    f"everyone living there." for n in range(1, sentences + 1))
    return {"subject": subject, "predicate": "procedures", "sources": [source],
            "value": {"heading": heading, "page": "https://example.edu/a/", "text": text}}


def test_a_long_reply_quotes_its_pages_shorter_and_then_names_them_never_cut() -> None:
    # 11-03 practice: a suitemate question's six sections came to 4,400 characters, past the
    # writer's limit, and the reply fell back to the evidence text with internal review notes.
    v = view(facts=[section(office, heading, 12, n) for office, n in (("Housing", 1),
                                                                      ("Ombuds", 2))
                    for heading in ("Contracts", "Rights", "Mediation")])
    full = plain_text(v)
    assert full is not None and len(full) > 3000 and full.count("says:") == 6
    shorter = plain_text(v, limit=3000)
    assert shorter is not None and len(shorter) < 3000 and shorter.count("says:") == 6
    assert shorter.count("(more on its page)") == 6
    # Tighter still: each subject quotes its first section, and names its others' pages.
    first = plain_text(v, limit=1600)
    assert first is not None and first.count("says:") == 2 and len(first) < 1600
    assert 'See Housing\'s page "Rights". See Housing\'s page "Mediation". [1]' in first
    # Only page names fit: each is named, and the reply is never cut or left to the evidence.
    names = plain_text(v, limit=200)
    assert names is not None and "says:" not in names
    assert names.startswith('See Housing\'s page "Contracts". See Housing\'s page "Rights". '
                            'See Housing\'s page "Mediation". [1]')
    assert unsupported_by(names, v) == []


def test_a_note_beside_the_hours_says_only_what_the_reply_has_not_said() -> None:
    hours = {"subject": "Library", "predicate": "hours_on", "day": "Tuesday",
             "date": "2026-11-03",
             "value": {"schedule": "Library (Main Building)", "hours": "7:45am-12:00am",
                       "notes": ["Please note that the front doors will be locked fifteen "
                                 "minutes before closing time."]},
             "sources": [2]}
    note = {"subject": "Library", "predicate": "contact_note", "purpose": "also_published",
            "sources": [1], "value": "If chat is not online, email refdesk@example.edu. Please "
                                     "note that the front doors will be locked fifteen minutes "
                                     "before closing time."}
    said = plain_text(view(derived_facts=[hours], facts=[note]))
    # 11-03 practice: the doors line was said twice, once from each page.
    assert said is not None and said.count("front doors") == 1
    assert said.endswith("Library: If chat is not online, email refdesk@example.edu. [1]")
    only_repeats = {**note, "value": "Please note that the front doors will be locked fifteen "
                                     "minutes before closing time."}
    assert "Library:" not in (plain_text(view(derived_facts=[hours], facts=[only_repeats]))
                              or "")


def asked_time(subject: str, source: int, open_at: dict[str, Any], hours: str,
               **more: Any) -> dict[str, Any]:
    return {"subject": subject, "predicate": "hours_on", "day": "Tuesday", "date": "2026-11-03",
            "open_at": open_at, "value": {"schedule": subject, "hours": hours},
            "sources": [source], **more}


def test_a_time_asked_is_answered_for_that_time_with_the_places_open_then_first() -> None:
    after = {"relation": "after", "at": "9:00pm"}
    v = view(derived_facts=[
        asked_time("The Atrium", 1, {**after, "state": "closed", "last_closes": "7:00pm"},
                   "Lunch: 11:00 AM - 02:00 PM; Dinner: 04:30 PM - 07:00 PM"),
        asked_time("Birch Tree Inn", 2, {**after, "state": "open", "periods": [
            {"label": "Late Night", "opens": "9:00pm", "closes": "11:00pm"}]},
            "Dinner: 05:00 PM - 08:00 PM; Late Night: 09:00 PM - 11:00 PM"),
        asked_time("Library", 1, {**after, "state": "open", "periods": [
            {"opens": "7:45am", "closes": "12:00am", "open_at_asked": True}]}, "7:45am-12:00am"),
        asked_time("Public Safety", 2, {**after, "state": "open", "all_day": True}, "24 hours"),
        asked_time("Starbucks", 1, {**after, "state": "closed"}, "Closed")])
    said = plain_text(v)
    # 11-03 "where can I get food after 9pm": each venue's whole day was said, the closed ones
    # first. Now the time is answered, and the places open then lead.
    assert said == (
        "After 9:00pm on Tuesday, November 3, Birch Tree Inn serves Late Night from 9:00pm to "
        "11:00pm. [2]\n\n"
        "After 9:00pm on Tuesday, November 3, Library is open until 12:00am. [1]\n\n"
        "Public Safety is open 24 hours on Tuesday, November 3. [2]\n\n"
        "The Atrium is not open after 9:00pm on Tuesday, November 3: it closes at 7:00pm. [1]\n\n"
        "Starbucks is closed on Tuesday, November 3. [1]")
    assert unsupported_by(said, v) == []


def test_open_at_a_time_says_open_or_closed_then_and_now_first_when_asked_too() -> None:
    v = view(derived_facts=[
        asked_time("Birch Tree Inn", 2, {"relation": "at", "at": "10:00pm", "state": "open",
                                         "closes": "11:00pm", "meal": "Late Night"},
                   "Dinner: 05:00 PM - 08:00 PM; Late Night: 09:00 PM - 11:00 PM",
                   open_now={"state": "closed", "now": "6:10pm", "next_opens": "9:00pm",
                             "next_meal": "Late Night"}),
        asked_time("Registrar", 1, {"relation": "at", "at": "8:30pm", "state": "closed"},
                   "8:30am-4:30pm"),
        asked_time("Library", 1, {"relation": "at", "at": "10:00am", "state": "open",
                                  "closes": "12:00am"}, "7:45am-12:00am")])
    said = plain_text(v)
    # The day's hours follow, so a time named for another reason ("I have class at 8:30pm")
    # still gets them.
    assert said == (
        "Birch Tree Inn is closed now (6:10pm). It opens next at 9:00pm for Late Night. "
        "Birch Tree Inn is open at 10:00pm on Tuesday, November 3, serving Late Night until "
        "11:00pm. Birch Tree Inn on Tuesday, November 3: Dinner: 05:00 PM - 08:00 PM; Late "
        "Night: 09:00 PM - 11:00 PM. [2]\n\n"
        "Library is open at 10:00am on Tuesday, November 3, until 12:00am. Library on Tuesday, "
        "November 3: 7:45am-12:00am. [1]\n\n"
        "Registrar is closed at 8:30pm on Tuesday, November 3. Registrar on Tuesday, November 3: "
        "8:30am-4:30pm. [1]")
    assert unsupported_by(said, v) == []
    # Listed hours whose dates are not published are said as listed, with why.
    listed = view(derived_facts=[{
        "subject": "Registrar", "predicate": "hours_on", "day": "Tuesday", "date": "2026-11-03",
        "listed_open_at": {"relation": "after", "at": "9:00pm", "state": "closed",
                           "last_closes": "4:30pm"},
        "applicability": "unverified",
        "applicability_reason": "The page does not say which dates this schedule covers.",
        "current": False, "value": {"schedule": "Registrar", "hours": None,
                                    "listed_hours": "8:30am-4:30pm"}, "sources": [1]}])
    assert plain_text(listed) == (
        "By its listed hours, Registrar is not open after 9:00pm on Tuesday, November 3: it "
        "closes at 4:30pm. [1]\n\nThe page does not say which dates this schedule covers. [1]")
    # A shape it does not know is left to the code's evidence text.
    odd = view(derived_facts=[asked_time("Registrar", 1, {"relation": "before", "at": "8:30pm",
                                                          "state": "closed"}, "8:30am-4:30pm")])
    assert plain_text(odd) is None


def test_a_page_not_read_again_since_its_freshness_ran_out_is_said_with_the_day_it_was_read(
        ) -> None:
    # 11-03 repeat test, "where do I find all the student orgs?": the clubs pages were last read
    # September 26, and the reply fell back to the evidence text with its internal notes.
    def stale(text: str, source: int = 2) -> dict[str, Any]:
        return {"subject": "Center for Student Involvement", "predicate": "procedures",
                "current": False, "sources": [source],
                "value": {"heading": "Overview", "page": "https://example.edu/b/", "text": text}}

    def dated(**parts: Any) -> dict[str, Any]:
        v = view(asked={"intent": "procedure", "offices": [], "fields": [],
                        "asOf": "2026-11-03T15:00:00-05:00"}, **parts)
        v["sources"][1] = {**v["sources"][1], "captured_at": "2026-09-26T10:32:16-04:00",
                           "freshness": "stale"}
        return v

    v = dated(facts=[stale("Students who do not see their interests should ask about starting "
                           "a new club.")])
    said = plain_text(v)
    assert said == ('Center for Student Involvement\'s page "Overview", as read on September 26, '
                    'says: "Students who do not see their interests should ask about starting a '
                    'new club." [2]')
    assert unsupported_by(said, v) == []
    # Read in another year, the year is said; named only, the page says when it was read.
    old = dated(facts=[stale("Ask about starting a new club.")])
    old["sources"][1]["captured_at"] = "2025-09-26T10:32:16-04:00"
    assert "as read on September 26, 2025, says:" in (plain_text(old) or "")
    named = plain_text(dated(facts=[stale(" ".join(["Ask about starting a new club."] * 40))]),
                       limit=120)
    assert named == ('See Center for Student Involvement\'s page "Overview" (read on '
                     'September 26). [2]')
    # With no date to say, it is left to the code's evidence text.
    assert plain_text(view(facts=[stale("Ask about starting a new club.")])) is None


def test_rooms_with_a_label_or_a_map_link_are_said_in_words() -> None:
    # Fix check 11-04: 19 offices' rooms carry a label or a campus map link, and a room in a
    # shape code did not know was printed as it is ("{'label': 'Main Office', ...}").
    from rockygpt_brain.plain_writer import room_parts
    assert room_parts(["C-209", {"label": "Main Office", "location": "C-209"},
                       {"name": "Fieldhouse", "map": "https://www.ramapo.edu/map/"},
                       {"location": " D-216 "}]) == (
        ["C-209", "Fieldhouse", "D-216"], [("Main Office", "C-209")], False)
    assert room_parts([{"room": "C-1"}, ""]) == ([], [], True)
    v = view(facts=[{"subject": "Athletics", "predicate": "offices", "value": [
        {"name": "Bill Bradley Sports and Recreation Arena",
         "map": "https://www.ramapo.edu/map/?id=1"}], "sources": [1]}])
    said = plain_text(v)
    assert said == "Athletics is in Bill Bradley Sports and Recreation Arena. [1]"
    assert unsupported_by(said, v) == []


def test_a_published_name_that_is_its_own_name_and_an_absence_already_answered_are_unsaid() -> None:
    # Fix check 11-04: "Title IX's published name is Title IX." and "Title IX doesn't publish a
    # general email address" beside titleix@ramapo.edu from its contact note.
    v = view(facts=[
        {"subject": "Title IX", "predicate": "name", "value": "Title IX", "sources": [1]},
        {"subject": "Title IX", "predicate": "contact_note",
         "value": "To file a complaint: titleix@ramapo.edu", "sources": [1]}],
        not_published=[{"subject": "Title IX", "predicate": "email", "checked_at": CHECKED,
                        "pages": [], "sources": [1]},
                       {"subject": "Title IX", "predicate": "phones", "checked_at": CHECKED,
                        "pages": [], "sources": [1]}])
    said = plain_text(v)
    assert said is not None
    assert "published name" not in said and "general email" not in said
    assert "titleix@ramapo.edu" in said and "doesn't publish a general phone" in said
