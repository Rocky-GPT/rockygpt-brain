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
