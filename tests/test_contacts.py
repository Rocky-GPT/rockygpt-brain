"""Office contacts written by code (contacts.py): a question that only asks how to reach offices
it names, and the offices a "who do I go to" reply sends the student to."""

from rockygpt_brain import contacts
from test_one_call import PartsGateway, ask, one_reply

OFFICES = [
    {"id": "hs", "name": "Health Services", "aliases": ["Health Center", "Valley Medical Group"]},
    {"id": "hp", "name": "Health Promotion", "aliases": []},
    {"id": "cc", "name": "Counseling Center", "aliases": ["Counseling Services"]},
    {"id": "ps1", "name": "Public Safety (Non-Emergency)", "aliases": ["Public Safety",
                                                                       "Campus Police"]},
    {"id": "ps2", "name": "Public Safety (Emergency)", "aliases": ["Public Safety"]},
    {"id": "sa", "name": "Student Accounts", "aliases": ["Bursar"]},
]


def named(message: str) -> list[str]:
    return [o["id"] for o in contacts.offices_named(OFFICES, message)[0]]


def test_offices_are_named_by_name_other_name_or_name_less_its_generic_word() -> None:
    assert named("how do I reach the health center and counseling") == ["hs", "cc"]
    assert named("how do i contact campus police") == ["ps1"]
    assert named("how do i contact public safety") == ["ps1", "ps2"]
    assert named("how do i reach the bursar") == ["sa"]
    # "Health" alone is the start of Health Promotion too: it names neither.
    assert named("is my health insurance accepted") == []
    # Inside a longer name the message holds, a shorter one is not named.
    assert named("what is health promotion's email") == ["hp"]
    assert named("my bike got stolen, who do i report it to") == []


def test_only_a_question_asking_how_to_reach_the_named_offices_is_contact_only() -> None:
    def only(message: str) -> bool:
        return contacts.asks_contact_only(message, contacts.offices_named(OFFICES, message)[1])
    assert only("how do I reach the health center and counseling")
    assert only("what's the phone number for the bursar")
    assert only("where is the counseling center")
    assert only("what is health promotion's email")
    assert not only("how do I make an appointment at the health center")
    assert not only("is counseling free")
    assert not only("counseling center")  # Asks nothing about reaching it.


def test_who_to_go_to_is_asked() -> None:
    assert contacts.asks_who("my bike got stolen near the student center, who do i report it to")
    assert contacts.asks_who("Who should I talk to about a roommate problem?")
    assert contacts.asks_who("which office do I go to for a parking ticket")
    assert not contacts.asks_who("how do I make an appointment at the health center")


def test_an_offices_contacts_in_codes_words() -> None:
    said = contacts.contact_text(
        "Public Safety (Emergency)", ["publicsafety@ramapo.edu"],
        [[{"number": "+12016846666"}, {"type": "Director of Public Safety",
                                        "number": "+12016847789"}]], [["C-102"]], 2)
    assert said == ("**Public Safety (Emergency)**: Phone: (201) 684-6666. "
                    "Email: publicsafety@ramapo.edu. Office: C-102. [2]")
    # Staff numbers only when the office publishes none of its own; a place is not a room.
    assert contacts.contact_text("Health Services", [], [[{"type": "Front desk",
                                                           "number": "+12016847536"}]],
                                 [["Near the South Gate"]], 1) == (
        "**Health Services**: Phone: Front desk (201) 684-7536. Location: Near the South Gate. [1]")
    assert contacts.contact_text("X", [], [], [], 1) is None


def test_how_to_reach_two_named_offices_is_answered_by_code_with_no_model_call() -> None:
    gateway = PartsGateway()
    result = ask(gateway, "How do I reach the Library and Academic Advising?")
    assert result.trace is not None and result.trace["decidedBy"] == "code"
    assert gateway.inputs == [] and gateway.embedded == []
    body = result.body["answer"].split("\n\nSources:")[0]
    assert body == ("- **Library**: Email: library@example.edu. [1]\n"
                    "- **Academic Advising**: Email: advising@example.edu. [2]")


def test_who_to_go_to_gets_the_cited_offices_contacts_after_the_reply() -> None:
    gateway = PartsGateway(one_reply("Ask Academic Advising about tutoring. [2]", [2]))
    result = ask(gateway, "who do i talk to about becoming a peer tutor")
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"
    body = result.body["answer"].split("\n\nSources:")[0]
    # Section 2 is Academic Advising's: its directory contacts follow, in code's words.
    assert body.startswith("Ask Academic Advising about tutoring. [2]\n\n"
                           "**Academic Advising**: Email: advising@example.edu. [")
    # The model was told code adds the contacts.
    assert '"contacts_written_by_code": true' in gateway.inputs[-1][1]["content"]


def test_every_reply_gets_the_cited_offices_contacts_from_code() -> None:
    # 11-03 repeat test: the writer chose which contacts to give, so the same how-to question
    # got different phone numbers, emails and rooms, or none, from try to try.
    gateway = PartsGateway(one_reply("Academic Advising runs the tutor program. [2]", [2]))
    result = ask(gateway, "how do i become a peer tutor")
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"
    body = result.body["answer"].split("\n\nSources:")[0]
    assert body.startswith("Academic Advising runs the tutor program. [2]\n\n"
                           "**Academic Advising**: Email: advising@example.edu. [")
    assert '"contacts_written_by_code": true' in gateway.inputs[-1][1]["content"]


def test_a_link_with_or_without_its_trailing_slash_is_the_same_page() -> None:
    from rockygpt_brain.writer import unsupported_by
    view = {"facts": [{"text": "Report An Incident Here (https://x.edu/report-form)"}]}
    assert unsupported_by("Use the [form](https://x.edu/report-form/). ", view) == []
    assert unsupported_by("Use https://x.edu/report-form.", view) == []
    assert unsupported_by("Use https://x.edu/other-form/.", view) != []


def test_only_offices_the_reply_sends_the_student_to_get_their_contacts() -> None:
    from rockygpt_brain.engine import ChatEngine
    from test_meaning import meaning_service
    engine = ChatEngine(PartsGateway(), meaning_service(), output="written", howto_one_call=True)
    offices = [{"office": "Academic Advising", "sections": [{"n": 1}]},
               {"office": "Center for Reading and Writing", "sections": [{"n": 2}]},
               {"office": "Library", "sections": [{"n": 3}]}]
    def ids(written: str) -> list[str]:
        return [o["id"] for o in engine._cited_offices(written, offices)]
    # The first cited; another only when the reply also names it.
    assert ids("Ask your advisor. [1] See the handbook. [3]") == ["advising"]
    assert ids("Ask your advisor [1], or the Library [3].") == ["advising", "library"]
    assert ids("Tutors help. [2][1]") == ["tutoring"]


def test_an_office_comes_with_the_offices_sharing_its_name_but_a_part_in_parentheses() -> None:
    # 11-03: the bike-theft steps said 684-6666 and the card gave only the non-emergency
    # 684-7432; both Public Safety cards, named Emergency and Non-Emergency, say which is which.
    chosen = [OFFICES[3]]
    assert [o["id"] for o in contacts.with_siblings(chosen, OFFICES)] == ["ps1", "ps2"]
    assert [o["id"] for o in contacts.with_siblings([OFFICES[0]], OFFICES)] == ["hs"]


def test_the_words_call_an_office_with_siblings_by_the_name_they_share() -> None:
    # 11-03 Mac run 3: "Report it to Public Safety (Non-Emergency)... Call (201) 684-6666"
    # pinned the emergency line on the non-emergency name; the contact lines say which is which.
    both = contacts.with_siblings([OFFICES[3]], OFFICES)
    written = "Report it to Public Safety (Non-Emergency). Call (201) 684-6666. [2]"
    assert contacts.without_variant_names(written, both) == (
        "Report it to Public Safety. Call (201) 684-6666. [2]")
    # An office with no sibling keeps its own name.
    assert contacts.without_variant_names("Public Safety (Non-Emergency) [2]",
                                          [OFFICES[3]]) == "Public Safety (Non-Emergency) [2]"


def test_labelled_rooms_and_map_buildings_are_said_in_words() -> None:
    # Fix check 11-04: "Office: {'label': 'Main Office', 'location': 'C-209'}".
    said = contacts.contact_text(
        "Career Services", ["careers@ramapo.edu"], [[{"number": "+12016847440"}]],
        [[{"label": "Main Office", "location": "C-209"},
          {"label": "ASB Office", "location": "ASB-513"}]], 3)
    assert said == ("**Career Services**: Phone: (201) 684-7440. Email: careers@ramapo.edu. "
                    "Main Office: C-209; ASB Office: ASB-513. [3]")
    assert contacts.contact_text(
        "Athletics", [], [], [[{"name": "Bill Bradley Arena", "map": "https://x.edu/map"}]],
        1) == "**Athletics**: Location: Bill Bradley Arena. [1]"
    # A room in a shape code does not know is left out, never printed.
    assert contacts.contact_text("X", [], [], [[{"room": "C-1"}]], 1) is None
