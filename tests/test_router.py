"""Plain lookups routed by code with no model call (router.py)."""

from rockygpt_brain.router import code_route

OFFICES = [{"name": "Registrar"}, {"name": "Library"}, {"name": "Counseling Center"},
           {"name": "IT Help Desk", "also_called": ["Help Desk"]}, {"name": "Financial Aid"},
           {"name": "Public Safety (Emergency)", "also_called": ["Public Safety"]},
           {"name": "Public Safety (Non-Emergency)", "also_called": ["Public Safety"]},
           {"name": "Scholarships", "topic": True}]
VENUES = [{"name": "Dunkin'"}, {"name": "Birch Tree Inn", "also_called": ["Birch"]}]


def route(message: str) -> dict | None:
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


def test_anything_code_is_not_sure_of_is_left_to_the_model() -> None:
    assert route("Can I book an appointment with the Registrar?") is None  # words left over
    assert route("What is the phone number for Public Safety?") is None  # a shared name
    assert route("Registrar and Financial Aid phone numbers") is None  # two places
    assert route("Tell me about the Registrar") is None  # nothing it routes is asked
    assert route("What is the menu at the Library?") is None  # an office has no menu
    assert route("How do I apply for Scholarships?") is None  # a topic's page is read
    assert route("What is for dinner tonight?") is None  # no place named
