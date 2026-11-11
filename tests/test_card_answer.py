"""What code says from one answer card (card_answer.py)."""

from typing import Any

import pytest

from rockygpt_brain import card_answer

PAGE = "https://www.ramapo.edu/publicsafety/how-to-apply-for-a-parking-permit/"
LINK = "https://rcnjpublicsafety.omnigo.one/CESIReportExec/opr/OPRMain.aspx?IsAuth=1&groupid=102"


def card(**changes: Any) -> dict[str, Any]:
    return {
        "id": "c0123456789ab", "status": "ready", "flags": [], "notes": [],
        "owners": [{"entity_id": "ps", "name": "Public Safety", "kind": "office"}],
        "task": {"key": "e0", "task": "Apply for a parking permit", "applies_to": "students",
                 "steps": [
                     {"text": "Register for a parking permit using the registration link.",
                      "quote": f"Click Here for Parking Permit Registration ({LINK})"},
                     {"text": "The parking fee is charged to your term bill",
                      "quote": "The parking fee automatically appears as a charge"}]},
        "page": {"record_id": "r1", "url": PAGE, "updated": "2026-08-17"},
        "label": None, "over_now": None, "passed": [], **changes}


def test_a_card_is_said_as_its_winning_pages_steps_each_citing_the_page() -> None:
    text, sources = card_answer.say(card())
    assert text == (
        "**Apply for a parking permit**\n\n"
        "Who it is for: students.\n\n"
        # The page gave the link beside the step that names none: it is said with the step.
        f"1. Register for a parking permit using the registration link. Link: {LINK} [1]\n"
        "2. The parking fee is charged to your term bill. [1]")
    assert sources == [{"n": 1, "url": PAGE, "title": "Apply for a parking permit"}]


def test_one_step_is_a_sentence_and_a_term_is_named() -> None:
    task = {"key": "e3", "task": "Pick up a student parking permit", "term": "Fall 2026",
            "steps": [{"text": "Pick it up at the ID Office.", "quote": "ID Office"}]}
    text, sources = card_answer.say(card(task=task), numbered_from=4)
    assert text == ("**Pick up a student parking permit**\n\nTerm: Fall 2026.\n\n"
                    "Pick it up at the ID Office. [5]")
    assert sources[0]["n"] == 5


def test_a_link_a_step_or_the_source_already_gives_is_not_said_twice() -> None:
    task = {"key": "e0", "task": "Register",
            "steps": [{"text": f"Register at {LINK}.", "quote": f"Register ({LINK})"},
                      {"text": "Then read the page.", "quote": f"See ({PAGE}) or ({LINK})."},
                      {"text": "Pay.", "quote": "Pay (https://a.example/pay), "
                                                "(https://b.example/pay) or (https://c.example)"}]}
    text, _ = card_answer.say(card(task=task))
    assert text.count(LINK) == 1 and "Link: " + PAGE not in text
    # At most two of a step's links.
    assert "3. Pay. Link: https://a.example/pay https://b.example/pay [1]" in text
    assert "c.example" not in text


def test_a_card_for_a_date_that_passed_says_so_before_the_steps() -> None:
    passed = card(status="date_passed", label="Summer 2018", over_now="Summer 2018",
                  task={"key": "e1", "task": "Know MBA parking permit fees", "term": "2017-2018",
                        "steps": [{"text": "Summer permit: $106.88.", "quote": "$106.88"}]})
    text, _ = card_answer.say(passed)
    assert text == ("**Know MBA parking permit fees**\n\n"
                    "This is for Summer 2018, which has passed. The page said:\n\n"
                    "Summer permit: $106.88. [1]")


def test_passed_deadlines_of_left_out_pages_and_an_old_page_are_said_after_the_steps() -> None:
    found = card(notes=["old_page"], page={"url": PAGE, "updated": "2019-10-18"}, passed=[
        {"task": "Get a Fall 2026 meal plan refund", "label": "September 1, 2026",
         "url": "https://www.ramapo.edu/student-accounts/deadlines/"},
        {"task": "A task whose page has no address", "label": "May 1, 2026", "url": None}])
    text, sources = card_answer.say(found)
    assert text.endswith(
        "\n\nThis page last changed on October 18, 2019.\n\n"
        "Already passed: Get a Fall 2026 meal plan refund (September 1, 2026). [2]")
    assert sources[1] == {"n": 2, "url": "https://www.ramapo.edu/student-accounts/deadlines/",
                          "title": "Get a Fall 2026 meal plan refund"}


def test_page_words_never_read_as_formatting_but_an_address_stays_whole() -> None:
    task = {"key": "e0", "task": "Use *bold* [words]",
            "steps": [{"text": "Open https://example.edu/a_b_c and #apply", "quote": "q"}]}
    text, _ = card_answer.say(card(task=task))
    assert "**Use \\*bold\\* \\[words\\]**" in text
    assert "Open https://example.edu/a_b_c and \\#apply. [1]" in text


@pytest.mark.parametrize(("changes", "why"), [
    ({}, None),
    ({"status": "date_passed", "label": "Fall 2022", "over_now": "Fall 2022"}, None),
    ({"status": "needs_look"}, "needs_look"),
    ({"status": "something new"}, "needs_look"),
    # Its date passed after the cards were built, or a new term came up on the page.
    ({"over_now": "September 1, 2026"}, "date_changed"),
    ({"status": "date_passed", "label": "Fall 2022", "over_now": None}, "date_changed"),
    ({"task": {"key": "e0", "task": "No steps", "steps": []}}, "no_steps"),
    ({"task": {"key": "e0", "task": "No steps", "steps": [{"text": " ", "quote": "q"}]}},
     "no_steps"),
    # A year's fee table read as 25 steps is not something to do: the page path answers.
    ({"task": {"key": "e0", "task": "Know the fees",
               "steps": [{"text": f"Fee {n}: $1.", "quote": "q"} for n in range(12)]}}, None),
    ({"task": {"key": "e0", "task": "Know the fees",
               "steps": [{"text": f"Fee {n}: $1.", "quote": "q"} for n in range(13)]}}, "too_long"),
    ({"page": {"url": None}}, "no_page"),
])
def test_code_does_not_answer_from_every_card(changes: dict[str, Any], why: str | None) -> None:
    assert card_answer.refusal(card(**changes)) == why
