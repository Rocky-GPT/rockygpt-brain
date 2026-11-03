"""Plain lookups routed by code with no model call (router.py)."""

from typing import Any

from rockygpt_brain.router import (
    asks_inside,
    code_route,
    places_named,
    rooms_said,
    shuttle_route,
    without_places,
)

OFFICES: list[dict[str, Any]] = [
    {"name": "Registrar"}, {"name": "Library"}, {"name": "Counseling Center"},
    {"name": "IT Help Desk", "also_called": ["Help Desk", "ITS"]}, {"name": "Financial Aid"},
    {"name": "Public Safety (Emergency)", "also_called": ["Public Safety"]},
    {"name": "Public Safety (Non-Emergency)", "also_called": ["Public Safety"]},
    {"name": "Scholarships", "topic": True}]
VENUES: list[dict[str, Any]] = [{"name": "Dunkin'", "publishes": ["hours"]},
                                {"name": "Birch Tree Inn", "also_called": ["Birch"],
                                 "publishes": ["hours", "menu"]}]


def route(message: str) -> dict[str, Any] | None:
    found = code_route(message, OFFICES, VENUES)
    return found["requests"][0] if found else None


def test_a_listed_name_and_plain_words_for_what_is_asked_are_routed_by_code() -> None:
    assert route("What is the email address for the Registrar?") == {
        "query": "Registrar", "fields": ["email"]}
    assert route("Is the library open right now?") == {
        "query": "Library", "fields": ["hours"], "day": "today"}
    assert route("Where is the Counseling Center?") == {
        "query": "Counseling Center", "fields": ["offices"]}
    assert route("How do I contact the IT Help Desk?") == {
        "query": "IT Help Desk", "fields": ["email", "phones", "offices"]}
    assert route("What is the fax number for Financial Aid?") == {
        "query": "Financial Aid", "fields": ["phones"], "phone_kind": "fax"}
    assert route("When does Dunkin' close on Saturday?") == {
        "query": "Dunkin'", "fields": ["hours"], "day": "saturday"}
    assert route("What's for dinner at the Birch Tree Inn tonight?") == {
        "query": "Birch Tree Inn", "fields": ["menu"], "day": "today", "meal": "dinner"}


def test_a_possessive_name_and_two_fields_of_one_place_are_routed_by_code() -> None:
    assert route("Where is the Registrar's office and what are its hours?") == {
        "query": "Registrar", "fields": ["offices", "hours"]}
    assert route("What is the Registrar’s email?") == {"query": "Registrar", "fields": ["email"]}
    # "its" is a word, not the IT Help Desk's acronym ITS, which is a name only in capitals.
    assert route("What is the phone number for ITS?") == {"query": "IT Help Desk",
                                                          "fields": ["phones"]}


def test_anything_code_is_not_sure_of_is_left_to_the_model() -> None:
    assert route("Can I book an appointment with the Registrar?") is None  # words left over
    assert route("Registrar and Financial Aid phone numbers") is None  # two places
    assert route("Tell me about the Registrar") is None  # nothing it routes is asked
    assert route("What is the menu at the Library?") is None  # an office has no menu
    assert route("How do I apply for Scholarships?") is None  # a topic's page is read
    assert route("Is anything open tonight?") is None  # no place named, and not a menu


def test_a_plain_next_first_or_last_shuttle_question_is_routed_by_code() -> None:
    assert shuttle_route("When is the last shuttle tonight?") == {
        "requests": [{"stop": None, "which": "last", "day": None}]}
    assert shuttle_route("What time does the first bus leave campus tomorrow?") == {
        "requests": [{"stop": None, "which": "first", "day": "tomorrow"}]}
    assert shuttle_route("when is the next shuttle on saturday") == {
        "requests": [{"stop": None, "which": "next", "day": "saturday"}]}
    for more in ("When is the last shuttle to the train station?",  # A stop: the model matches it.
                 "When is the last shuttle after my 6pm class?",
                 "When does the shuttle run?",  # No next, first or last.
                 "What's the first and last shuttle today?"):
        assert shuttle_route(more) is None, more


def test_a_name_offices_share_looks_up_each_of_them() -> None:
    assert code_route("What is the phone number for Public Safety?", OFFICES, VENUES) == {
        "requests": [{"query": "Public Safety (Emergency)", "fields": ["phones"]},
                     {"query": "Public Safety (Non-Emergency)", "fields": ["phones"]}]}


def test_a_menu_asked_of_no_place_is_the_menu_of_every_venue_that_publishes_one() -> None:
    assert route("What's on the menu for lunch today?") == {
        "query": "Birch Tree Inn", "fields": ["menu"], "day": "today", "meal": "lunch"}
    assert route("What is for dinner tonight?") == {
        "query": "Birch Tree Inn", "fields": ["menu"], "day": "today", "meal": "dinner"}


def test_who_to_contact_for_help_with_an_offices_service_label_is_routed_by_code() -> None:
    # 11-03: "Canvas" is the Instructional Design Center's own label for the service it supports.
    offices = [*OFFICES, {"name": "Instructional Design Center",
                          "also_called": ["IDC", "Canvas"]}]
    assert code_route("Who do I contact for help with Canvas?", offices, VENUES) == {
        "requests": [{"query": "Instructional Design Center",
                      "fields": ["email", "phones", "offices"]}]}
    assert code_route("Who can help me with Canvas quizzes?", offices, VENUES) is None
    assert code_route("Who is the director of the IDC?", offices, VENUES) is None


def test_a_number_sign_asks_for_the_number() -> None:
    # 11-03 practice: "#" stands for "number", so "registrar # ?" asks the Registrar's phone.
    assert route("registrar # ?") == {"query": "Registrar", "fields": ["phones"]}
    assert route("financial aid #") == {"query": "Financial Aid", "fields": ["phones"]}


def test_the_places_a_message_names_are_found_however_it_is_typed() -> None:
    assert places_named("counseling center???", OFFICES, VENUES) == [
        {("office", "Counseling Center")}]
    # A name a few offices share is one name; a name inside a longer one is not a name.
    assert places_named("public safety", OFFICES, VENUES) == [
        {("office", "Public Safety (Emergency)"), ("office", "Public Safety (Non-Emergency)")}]
    assert places_named("it help desk!!", OFFICES, VENUES) == [{("office", "IT Help Desk")}]
    assert places_named("birch rn??", OFFICES, VENUES) == [{("venue", "Birch Tree Inn")}]
    # Mashed keys, a topic with no office of its own, and "its" in lower case name no place.
    assert places_named("jdjdjsjskskaka", OFFICES, VENUES) == []
    assert places_named("scholarships", OFFICES, VENUES) == []
    assert places_named("its fine", OFFICES, VENUES) == []


def test_the_words_left_when_the_places_a_message_names_are_taken_out() -> None:
    # What is asked of a place, not where: the words an event title must hold (events.py).
    assert without_places("can I get a flu shot at the Counseling Center?", OFFICES,
                          VENUES) == "can i get a flu shot at the"
    assert without_places("what's at the Birch's grill", OFFICES, VENUES) == "what at the grill"
    assert without_places("anything about narcan", None, None) == "anything about narcan"


BUILDINGS: list[dict[str, Any]] = [
    {"name": "Student Center (SC)", "also_called": ["SC", "Student Center"]},
    {"name": "Peter P. Mercer Learning Commons", "also_called": ["Learning Commons"]},
    {"name": "Laurel Hall North Building", "also_called": ["Laurel Hall"]},
    {"name": "Laurel Hall South Building", "also_called": ["Laurel Hall"]},
    # The map's building of the same name as an office: the office is what it names.
    {"name": "Library"}]


def test_a_building_is_routed_by_code_when_asked_where_it_is_or_what_is_open_in_it() -> None:
    # 11-03 practice: "where da student center at" found nothing, as no office is called that.
    def building(message: str) -> dict[str, Any] | None:
        return code_route(message, OFFICES, VENUES, BUILDINGS)

    assert building("Where is the Student Center?") == {
        "requests": [{"query": "Student Center (SC)", "fields": ["offices"]}]}
    assert building("Is the Learning Commons open on Sunday?") == {"requests": [
        {"query": "Peter P. Mercer Learning Commons", "fields": ["hours"], "day": "sunday"}]}
    # A building has no menu, phone or email of its own, and two buildings of one name are
    # for the model to ask about.
    assert building("What's for lunch in the student center?") is None
    assert building("How do I contact the Student Center?") is None
    assert building("What is the phone number for the Student Center?") is None
    assert building("Where is Laurel Hall?") is None
    # A building named like an office is the office.
    assert building("Where is the library?") == {
        "requests": [{"query": "Library", "fields": ["offices"]}]}
    assert places_named("student center rn", OFFICES, VENUES, BUILDINGS) == [
        {("building", "Student Center (SC)")}]
    assert places_named("the library", OFFICES, VENUES, BUILDINGS) == [{("office", "Library")}]


def test_what_is_in_a_building_is_its_places() -> None:
    # 11-03 practice: "what offices are in academic building d" went to the model, which listed
    # three of D's offices from pages; the building's where-answer names its places.
    def building(message: str) -> dict[str, Any] | None:
        return code_route(message, OFFICES, VENUES, BUILDINGS)

    where = {"requests": [{"query": "Student Center (SC)", "fields": ["offices"]}]}
    assert building("what offices are in the student center") == where
    assert building("What's in the Student Center?") == where
    assert building("which offices are inside the student center") == where
    # Hours asked of it are still its places' hours, and an office has no places in it.
    assert building("what is open in the student center") == {
        "requests": [{"query": "Student Center (SC)", "fields": ["hours"]}]}
    assert route("what is in the registrar") is None


def test_a_message_asks_what_is_in_the_building_it_names() -> None:
    d = {"name": "Academic Building D", "also_called": []}
    sc = {"name": "Student Center (SC)", "also_called": ["SC", "Student Center"]}
    assert asks_inside("what offices are in academic building d", d)
    assert asks_inside("What's inside the Student Center?", sc)
    assert asks_inside("which places are in the SC", sc)
    # Where it is, its hours, or something more than what is in it, is not.
    assert not asks_inside("Where is Academic Building D?", d)
    assert not asks_inside("is the student center open on saturday", sc)
    assert not asks_inside("what offices are in academic building d and when do they open", d)
    # Another building named is not this one.
    assert not asks_inside("what offices are in academic building d", sc)


ROOMED: list[dict[str, Any]] = [
    {"name": "Anisfield School of Business (ASB)",
     "also_called": ["ASB", "Anisfield School of Business"], "room_prefixes": ["ASB"]},
    {"name": "Academic Building A", "also_called": [], "room_prefixes": ["A"]},
    {"name": "Academic Building D", "also_called": [], "room_prefixes": ["D"]},
    {"name": "Berrie Center", "also_called": [], "room_prefixes": ["BC"]},
    {"name": "Student Center (SC)", "also_called": ["SC", "Student Center"],
     "room_prefixes": ["SC"]},
    {"name": "Peter P. Mercer Learning Commons", "also_called": ["Learning Commons"],
     "room_prefixes": ["LC", "LIB", "LIBRARY"]}]
# The business school's office is also called ASB, like its building.
WITH_ASB = [*OFFICES, {"name": "Anisfield School of Business", "also_called": ["ASB"]}]


def test_a_room_said_by_its_prefix_names_its_building_and_asks_where_it_is() -> None:
    # 11-03 repeat test: "where tf is ASB 407?" got the business school's office (ASB-333) or a
    # page of room diagrams, never the building whose room numbers start with ASB.
    def room(message: str) -> str | None:
        found = code_route(message, WITH_ASB, VENUES, ROOMED)
        if found is None:
            return None
        assert [r["fields"] for r in found["requests"]] == [["offices"]]
        return str(found["requests"][0]["query"])

    for message in ("Where is ASB 407?", "where is asb-407", "How do I get to ASB407?",
                    "ASB 407?", "Where is room ASB 407?"):
        assert room(message) == "Anisfield School of Business (ASB)", message
    for message in ("Where is D-224?", "where is d-224", "Where is D224?"):
        assert room(message) == "Academic Building D", message
    assert room("Where is room SC-219A?") == "Student Center (SC)"
    assert room("where is LIBRARY 101") == "Peter P. Mercer Learning Commons"
    # The prefix alone is still the office of that name, and a room is asked only where it is.
    assert route_with_asb("Where is ASB?") == {"query": "Anisfield School of Business",
                                                "fields": ["offices"]}
    assert room("is ASB 407 open?") is None
    assert room("Where is LC 101 and LC 102?") is None  # Two rooms are for the model.
    assert places_named("ASB 407", WITH_ASB, VENUES, ROOMED) == [
        {("building", "Anisfield School of Business (ASB)")}]
    assert not asks_inside("what is in ASB 407", ROOMED[0])


def route_with_asb(message: str) -> dict[str, Any] | None:
    found = code_route(message, WITH_ASB, VENUES, ROOMED)
    return found["requests"][0] if found else None


def test_a_room_is_said_only_the_way_rooms_are_written() -> None:
    asb, a, _, bc, sc, lc = ROOMED
    assert rooms_said("where is asb 407 or ASB-210", asb) == ["ASB 407", "ASB-210"]
    assert rooms_said("where is sc-219a", sc) == ["SC-219A"]
    # Two letters or one are a prefix only in capitals or before a hyphen: "bc" is "because".
    assert rooms_said("bc 20 people are going", bc) == []
    assert rooms_said("where is sc 219", sc) == []
    # "A" beginning a sentence is the article, and a room number is no time, date or "24/7".
    assert rooms_said("A 300-level course", a) == []
    assert rooms_said("Is there a 300 level course", a) == []
    assert rooms_said("Where is A 204?", a) == ["A 204"]
    assert rooms_said("is the library 24/7", lc) == []
    assert rooms_said("meet at SC 12:30", sc) == []
