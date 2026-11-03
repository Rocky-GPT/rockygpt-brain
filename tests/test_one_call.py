"""A how-to question answered by one model call from code-found evidence (one_call.py)."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.one_call import (
    chosen_units,
    parts_of,
    pick_messages,
    routes_here,
    search_text,
    with_picked,
)
from rockygpt_brain.provider import Completion, ToolCall, TurnBudget, Usage
from rockygpt_brain.turn import intake
from test_engine import NOW, ScriptedGateway, completion, finish
from test_meaning import index_of, meaning_service
from test_writer import writing


def one_reply(text: str, sections: list[int], not_on_pages: bool = False) -> Completion:
    arguments = {"parts": [{"asked": "how to become a tutor", "not_on_pages": not_on_pages,
                            "sections": sections}], "text": text}
    return Completion("", (ToolCall("w", "write_answer", arguments),),
                      [{"type": "function_call", "call_id": "w", "name": "write_answer",
                        "arguments": json.dumps(arguments)}], Usage(10, 10, 0))


class PartsGateway(ScriptedGateway):
    def __init__(self, *steps: Any) -> None:
        super().__init__(*steps)
        self.embedded: list[list[str]] = []

    async def embed_many(self, texts: Sequence[str], *, dimensions: int,
                         budget: TurnBudget) -> list[list[float]]:
        self.embedded.append(list(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]


def ask(gateway: Any, question: str) -> Any:
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": question}]})
    engine = ChatEngine(gateway, meaning_service(), output="written",
                        howto_one_call=True)
    return asyncio.run(engine.answer(intake(request, now=NOW), request))


def test_a_how_to_is_routed_here_and_menus_shuttles_and_plain_contacts_are_not() -> None:
    assert routes_here("how do i become a peer tutor")
    assert routes_here("can i get a refund on my meal plan if i withdraw")
    assert not routes_here("what's for lunch at the atrium")
    assert not routes_here("when is the next shuttle to the train station")
    assert not routes_here("registrar email")
    assert not routes_here("library hours")
    # What a place or office is, only its own pages say (11-02: "The name is Birch Tree Inn.").
    assert routes_here("What is the Birch Tree Inn?")
    assert routes_here("what does the office of specialized services do")
    assert not routes_here("What is the Birch Tree Inn serving for dinner?")
    assert not routes_here("What is the phone number for Public Safety?")
    assert not routes_here("What's on the menu for lunch today?")
    # A question's parts are searched apart, and a short follow-up with the question before it.
    assert parts_of("how do I apply for summer housing? and what does it cost") == [
        "how do I apply for summer housing? and what does it cost",
        "how do I apply for summer housing?", "and what does it cost"]
    assert search_text("and for grad students?", "how do i apply for graduation") == (
        "how do i apply for graduation\nand for grad students?")


def test_one_call_says_the_sections_code_found_with_their_sources() -> None:
    gateway = PartsGateway(one_reply(
        "Center for Reading and Writing: Apply to become a peer tutor with the online form by "
        "March 1. [1]", [1]))
    result = ask(gateway, "how do i become a peer tutor")
    text = result.body["answer"]
    assert "March 1. [1]" in text and "Sources: [1](https://www.ramapo.edu/crw/tutor/)" in text
    assert result.body["citations"][0]["url"] == "https://www.ramapo.edu/crw/tutor/"
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"
    assert len(gateway.inputs) == 1 and gateway.embedded == [["how do i become a peer tutor"]]


def test_a_reply_the_evidence_does_not_hold_goes_to_the_four_call_loop() -> None:
    lookup = completion("graph_lookup", {"requests": [
        {"query": "Center for Reading and Writing", "fields": ["procedures"],
         "about": "become a peer tutor"}]})
    pick = completion("pick_sections", {"picks": [{"lookup": 0, "sections": [0]}]})
    gateway = PartsGateway(one_reply("Email tutors@example.com by March 1. [1]", [1]),
                           lookup, pick, finish(), writing("Apply by March 1 [1]."))
    result = ask(gateway, "how do i become a peer tutor")
    assert result.trace is not None and result.trace["oneCall"] == {
        "fallback": "not_in_the_evidence"}
    assert result.trace["decidedBy"] != "one_call"
    assert "tutors@example.com" not in result.body["answer"]


def test_one_statement_the_evidence_does_not_hold_is_dropped_and_the_rest_kept() -> None:
    gateway = PartsGateway(one_reply(
        "Center for Reading and Writing: Apply with the online form by March 1. [1] "
        "Email tutors@example.com with questions.", [1]))
    result = ask(gateway, "how do i become a peer tutor")
    text = result.body["answer"]
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"
    assert "by March 1. [1]" in text and "tutors@example.com" not in text
    assert result.trace["oneCallDropped"] == ["Email tutors@example.com with questions."]
    assert len(gateway.inputs) == 1  # No four-call loop after it.


def test_statements_split_after_sentences_not_after_steps_titles_or_times() -> None:
    from rockygpt_brain.one_call import statements, without_unsupported
    text = ("Meet Dr. Reck within 20 days. [1] Hours are 8:30 A.M. – 4:30 P.M. Monday. [2]\n\n"
            "1. Call (201) 684-9999 now. [2]\n2. Submit the form. [1]")
    pieces = statements(text)
    assert "".join(pieces) == text
    assert [p for p in pieces if p.strip()] == [
        "Meet Dr. Reck within 20 days. [1]", "Hours are 8:30 A.M. – 4:30 P.M. Monday. [2]",
        "1. Call (201) 684-9999 now. [2]", "2. Submit the form. [1]"]
    kept = without_unsupported(text, lambda said: ["x"] if "9999" in said else [])
    assert kept is not None and "9999" not in kept[0] and "2. Submit the form. [1]" in kept[0]
    # More than two statements to drop, or everything cited, is not a reply worth keeping.
    assert without_unsupported(text, lambda said: ["x"] if "[" in said else []) is None


def test_a_part_no_section_answers_gets_a_second_look_and_one_more_reply() -> None:
    gateway = PartsGateway(
        one_reply("I don't have verified information about that.", []),
        one_reply("Center for Reading and Writing: Apply with the online form by March 1. [2]",
                  [2]))
    result = ask(gateway, "how do i become a peer tutor")
    assert gateway.embedded == [["how do i become a peer tutor"], ["how to become a tutor"]]
    assert len(gateway.inputs) == 2 and result.trace is not None
    assert result.trace["decidedBy"] == "one_call"
    assert result.trace["oneCallSecondLook"] == ["how to become a tutor"]
    # The second look gives only sections the first did not, numbered after the first look's.
    second = json.loads(gateway.inputs[1][1]["content"])["offices"]
    numbers = [s["n"] for o in second for s in o["sections"]]
    assert numbers == list(range(1, len(numbers) + 1))
    assert len({(s["page"], s["heading"], s["text"]) for o in second for s in o["sections"]}) == (
        len(numbers))
    assert "Sources: [2](" in result.body["answer"]


def test_a_reply_saying_the_pages_are_silent_gets_that_sentence_searched_again() -> None:
    gateway = PartsGateway(
        one_reply("Apply with the online form. [1] The pages don't say when the deadline is. [1]",
                  [1]),
        one_reply("Apply with the online form by March 1. [1]", [1]))
    result = ask(gateway, "how do i become a peer tutor")
    assert gateway.embedded[1] == ["The pages don't say when the deadline is."]
    assert result.trace is not None
    assert result.trace["oneCallSecondLook"] == ["The pages don't say when the deadline is."]
    assert "March 1" in result.body["answer"]
    # A reply that answered with nothing said to be missing gets no second look.
    once = PartsGateway(one_reply("Apply with the online form by March 1. [1]", [1]))
    ask(once, "how do i become a peer tutor")
    assert len(once.embedded) == 1


def test_the_offices_whose_cards_match_are_read_for_their_contacts() -> None:
    service = meaning_service()
    units: list[tuple[str, str, list[str], list[list[float]]]] = [
        ("page-advising", "0", ["advising"], [[0, 1, 0]]),
        ("page-tutoring", "0", ["tutoring"], [[1, 0, 0]])]
    service.index = index_of(units, service.index.names,
                             cards=[("library", [1, 0, 0]), ("advising", [0, 1, 0])])
    gateway = PartsGateway(one_reply("Apply with the online form by March 1. [1]", [1]))
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "how do i become a peer tutor"}]})
    engine = ChatEngine(gateway, service, output="written",
                        howto_one_call=True)
    asyncio.run(engine.answer(intake(request, now=NOW), request))
    given = json.loads(gateway.inputs[0][1]["content"])["offices"]
    # The library has no section the search chose, but its card matches: its contacts are given.
    (library,) = [o for o in given if o["office"] == "Library"]
    assert library["sections"] == [] and library["email"]


def test_one_page_gives_at_most_two_units_so_the_next_page_is_read() -> None:
    index = index_of([("page-a", "0", ["advising"], [[1, 0, 0]]),
                      ("page-a", "1", ["advising"], [[0.99, 0.01, 0]]),
                      ("page-a", "2", ["advising"], [[0.98, 0.02, 0]]),
                      ("page-b", "0", ["tutoring"], [[0.9, 0.1, 0]])])
    _, order = chosen_units(index, [[1, 0, 0]])
    assert order == [("page-a", "0"), ("page-a", "1"), ("page-b", "0")]


def test_a_part_no_page_can_hold_gets_no_second_look() -> None:
    gateway = PartsGateway(one_reply("I don't have verified information about your balance.", [],
                                     not_on_pages=True))
    result = ask(gateway, "how do i become a peer tutor")
    assert len(gateway.inputs) == 1 and result.trace is not None
    assert "oneCallSecondLook" not in result.trace


def test_the_sections_picked_by_heading_are_read_first() -> None:
    index = index_of([("page-a", "0", ["advising"], [[1, 0, 0]]),
                      ("page-b", "0", ["advising"], [[0.9, 0.1, 0]]),
                      ("page-c", "0", ["tutoring"], [[0, 1, 0]])], labels=True)
    pool_scores, pool = chosen_units(index, [[1, 0, 0]], 60, per_part=30, per_page=3)
    asked = pick_messages("how do i become a peer tutor", index, pool)[1]["content"]
    assert "3. page-c | heading page-c 0" in asked
    _, order = with_picked(index, [[1, 0, 0]], pool_scores, pool, {"n": [3, 99]})
    assert order[0] == ("page-c", "0") and len(order) == 3
    # Nothing valid picked: the usual choice.
    assert with_picked(index, [[1, 0, 0]], pool_scores, pool, {"n": []})[1][0] == ("page-a", "0")


def test_with_headings_published_the_pick_comes_before_the_answer() -> None:
    service = meaning_service()
    service.index = index_of([("page-advising", "0", ["advising"], [[0, 1, 0]]),
                              ("page-tutoring", "0", ["tutoring"], [[1, 0, 0]])],
                             service.index.names, labels=True)
    picked = Completion("", (ToolCall("p", "pick_units", {"n": [1]}),),
                        [{"type": "function_call", "call_id": "p", "name": "pick_units",
                          "arguments": json.dumps({"n": [1]})}], Usage(10, 2, 0))
    gateway = PartsGateway(picked, one_reply("Apply with the online form by March 1. [1]", [1]))
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "how do i become a peer tutor"}]})
    engine = ChatEngine(gateway, service, output="written",
                        howto_one_call=True)
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert gateway.inputs[0][1]["content"].startswith("Question: how do i become a peer tutor")
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"


def test_a_dated_change_on_a_page_is_judged_against_today() -> None:
    from datetime import date

    from rockygpt_brain.one_call import dated_statements
    offices = [{"sections": [{"n": 3, "text": "SSSW Interim Dean Kathleen Ray. Effective July 1, "
                                              "2026 – Dean Susan Hangen"},
                             {"n": 4, "text": "Chess Club is not currently active as of 6/12/25."},
                             {"n": 5, "text": "Starting January 5, 2027 parking forms move "
                                              "online."}]}]
    today = date(2026, 10, 25)
    facts = [f["fact"] for f in dated_statements(offices, today, "who is the sssw dean",
                                                 ["Susan Hangen"])]
    assert facts == ['A page says "Effective July 1, 2026 – Dean Susan Hangen" [3]: July 1, '
                     '2026 has passed, so this is in effect now.']
    facts = [f["fact"] for f in dated_statements(offices, today,
                                                 "is the chess club active? parking forms")]
    assert facts == [
        'A page says "Chess Club is not currently active as of 6/12/25" [4]: June 12, 2025 has '
        'passed, so this is in effect now.',
        'A page says "Starting January 5, 2027 parking forms move online" [5]: January 5, 2027 '
        'is still to come, so this is not in effect yet.']
    # A session that starts on a date, or a row of a table of dates, is no change (11-03: a
    # refund table read as "August 26, 2026 has passed, so this is in effect now").
    table = [{"sections": [{"n": 7, "text": "7 week class session beginning August 26, 2026 "
                                            "100 % Refund: August 28, 2026 50 % Refund: "
                                            "September 2, 2026"},
                           # A change that starts on a date still is one.
                           {"n": 8, "text": "Beginning August 26, 2026 refund requests are "
                                            "online only."}]}]
    assert [f["n"] for f in dated_statements(table, today, "do i get my refund money back")] == [8]


def test_grouped_marks_cite_every_source_they_name() -> None:
    from rockygpt_brain.one_call import reply, without_unsupported
    from rockygpt_brain.writer import cited, unsupported_by
    # 11-02: "[4,7]" was read as citing nothing, so the parking reply showed 2 of its 6 pages.
    sources = [{"n": n, "url": f"https://www.ramapo.edu/p{n}/", "title": f"Page {n}"}
               for n in range(1, 7)]
    text, citations = reply({"text": "Apply online. [3,6] Pick it up at C-102. [1, 4] "
                                     "The fee is on the term bill. [4–5]"}, sources)
    assert [c["id"] for c in citations] == [f"section-{n}" for n in (1, 3, 4, 5, 6)]
    assert text.endswith("Sources: " + " ".join(f"[{n}](https://www.ramapo.edu/p{n}/)"
                                                 for n in (1, 3, 4, 5, 6)))
    assert cited("[2] then [2, 3] then [7-9]") == [2, 3, 7, 8, 9]
    # A reply whose only marks are grouped still has cited statements left after a drop.
    kept = without_unsupported("Apply online through the parking portal and choose your status. "
                               "[3, 6] Call 555-0100. [1]", lambda said: "555" in said)
    assert kept == ("Apply online through the parking portal and choose your status. [3, 6]",
                    ["Call 555-0100. [1]"])
    view = {"facts": "Apply online.", "sources": [{"n": 3}, {"n": 6}]}
    assert unsupported_by("Apply online. [3, 6]", view) == []


def test_a_reply_naming_a_course_the_facts_do_not_hold_is_not_sent() -> None:
    from rockygpt_brain.writer import unsupported_by
    view = {"facts": "CHEM 425 prerequisites: complete any of BIOL 332 and CHEM 205; CHEM 213",
            "sources": [{"n": 1}]}
    assert unsupported_by("Take CHEM 213 before CHEM 425 [1].", view) == []
    assert unsupported_by("CHEM 425 needs CHEM 211 first.", view) == [
        "a course the facts do not hold: CHEM 211"]
    # A word of no subject the facts hold ("room 425") is not checked as a course.
    assert unsupported_by("It meets in room 425.", view) == []


def test_a_dean_a_persons_details_or_a_courses_facts_are_answered_by_code() -> None:
    from rockygpt_brain.one_call import code_kind, code_text
    course: tuple[list[Any], list[Any], list[dict[str, Any]]] = (
        [], [], [{"id": "c1", "name": "CMPS 147"}])
    assert code_kind("Who is the dean of the Anisfield School of Business?", [], None) == "leaders"
    assert code_kind("What is Professor Hangen's email?", [{"id": "p"}], None) == "person"
    assert code_kind("How many credits is CMPS 147?", [], course) == "course"
    # Asked with something more, or how to do something, the model writes it.
    # How to reach the dean is the dean's directory contacts, which code says too.
    assert code_kind("Who is the dean and how do I reach them?", [], None) == "leaders"
    assert code_kind("Who is the dean and how do I get a waiver?", [], None) is None
    assert code_kind("Who is the dean of the School of Science and Health?", [], None) \
        == "leaders"
    assert code_kind("How do I email Professor Hangen?", [{"id": "p"}], None) is None
    hangen = [{"id": "p", "name": "Susan Hangen"}]
    assert code_kind("Who is Professor Hangen?", hangen, None) == "person"
    assert code_kind("What does Professor Hangen teach?", hangen, None) == "person"
    assert code_kind("Who is Professor Hangen's advisee?", hangen, None) is None
    assert code_kind("Who is Dean Chen?", [{"id": "c", "name": "Dean Chen"}], None) == "person"
    assert code_kind("Is CMPS 147 hard?", [], course) is None
    assert code_text([{"fact": "CMPS 147 is 4 credits.", "n": 1},
                      {"fact": "CMPS 147 has no published prerequisites.", "n": 1}]) == (
        "CMPS 147 is 4 credits. [1] CMPS 147 has no published prerequisites. [1]")
    # A fact written for a writer, not a student, is never said as it is.
    assert code_text([{"fact": "3 people share the name. Ask which one the student means."}]) \
        is None

    from rockygpt_brain.one_call import course_facts_asked
    facts = [{"fact": "CMPS 147 is 4 credits.", "n": 1},
             {"fact": "CMPS 147 prerequisites, as the catalog states them: MATH 101", "n": 1}]
    assert course_facts_asked("How many credits is CMPS 147?", facts) == facts[:1]
    assert course_facts_asked("What is CMPS 147 about?", facts) == facts


def test_the_every_word_check_lists_words_the_cited_facts_do_not_hold() -> None:
    from rockygpt_brain.one_call import evidence_by_mark, unbacked
    offices = [{"office": "Registrar", "email": ["reg@example.edu"], "phones": [], "rooms": [],
                "sections": [{"n": 1, "heading": "Transcripts",
                              "text": "Order official transcripts online through the "
                                      "Clearinghouse."}]}]
    evidence = evidence_by_mark(offices, [])
    shared = "How do I get a transcript?"
    assert unbacked("Order official transcripts online through the Clearinghouse. [1]",
                    evidence, shared) == []
    # A word no cited fact holds ("free") is listed, and so is an uncited statement's.
    assert unbacked("Transcripts are free. [1] Pickup is same day.", evidence, shared) == [
        {"statement": "Transcripts are free. [1]", "words": ["free"]},
        {"statement": "Pickup is same day.", "words": ["day", "pickup"]}]


def test_a_mark_covers_its_paragraph_and_rewording_words_do_not_count() -> None:
    from rockygpt_brain.one_call import evidence_by_mark, unbacked
    offices = [{"office": "Registrar", "email": [], "phones": [], "rooms": [], "sections": [
        {"n": 1, "heading": "Steps", "text": "Fill out the form. Email it to the office."}]}]
    evidence = evidence_by_mark(offices, [])
    assert unbacked("The Registrar handles it.\n1. Fill out the form.\n2. Email it. [1]",
                    evidence, "") == []
    assert unbacked("Fill out the form. [1]\n\nIt costs nothing.", evidence, "") == [
        {"statement": "It costs nothing.", "words": ["cost", "noth"], "uncited": True}]


def emergency_service() -> Any:
    """The how-to pages, one of which says to call 911, and Public Safety's emergency line."""
    service = meaning_service()
    tutoring = next(p for p in service.records["office_sections"] if p["id"] == "page-tutoring")
    tutoring["sections"].append({"heading": "Worried about a student",
                                 "text": "If a student is in danger right now, call 911."})
    service.index = index_of(
        [("page-tutoring", "0", ["tutoring"], [[1, 0, 0]]),
         ("page-tutoring", "1", ["tutoring"], [[1, 0, 0]])], service.index.names)
    service.entities.append({"id": "ps-e", "name": "Public Safety (Emergency)", "kind": "office",
                             "aliases": [], "links": [{"collection": "contacts",
                                                       "source_key": "directory",
                                                       "source_record_keys": ["ps-e"]}]})
    service.contacts.append({"id": "source-ps-e", "source_key": "directory",
                             "source_record_key": "ps-e", "name": "Public Safety (Emergency)",
                             "phone": "(201) 555-6666",
                             "canonical_url": "https://example.edu/publicsafety",
                             "collected_at": NOW, "freshness_sla_hours": 24})
    return service


def test_a_reply_that_says_call_911_also_gives_public_safetys_published_line() -> None:
    # 11-03: "I'm worried about a student" said to call 911 but gave no campus number.
    from rockygpt_brain.engine import HELP_TEXT

    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "I'm worried about a student. Who do I contact?"}]})
    gateway = PartsGateway(one_reply("If the student is in danger right now, call 911. [2]", [2]))
    engine = ChatEngine(gateway, emergency_service(), output="written", howto_one_call=True)
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    text = result.body["answer"]
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"
    body, sources = text.split("\n\nSources: ")
    # "Who do I contact?": the cited office's contacts come next, written by code, then the line.
    assert body.startswith("If the student is in danger right now, call 911. [2]\n\n"
                           "**Center for Reading and Writing**: ")
    assert result.trace["codeParts"] == 1 and "\n\n" + HELP_TEXT in body
    assert "**Public Safety \\(Emergency\\)**: \\(201\\) 555-6666." in body
    assert "https://example.edu/publicsafety" in body and sources.startswith("[2](")
    assert "https://example.edu/publicsafety" in {c["url"] for c in result.body["citations"]}
    # A reply that already gives the line gets nothing more.
    from rockygpt_brain.answers import Rendered
    from rockygpt_brain.engine import with_campus_help
    line = Rendered("**Public Safety \\(Emergency\\)**: \\(201\\) 555-6666. [Source](https://x)",
                    [{"id": "s", "url": "https://x"}], True, True)
    said = "Call Public Safety at 201.555.6666 or 911. [1]\n\nSources: [1](https://y)"
    assert with_campus_help(said, [], [line]) == (said, [])
    # No 911, no campus line: a how-to that names no emergency is left as written.
    plain = ask(PartsGateway(one_reply(
        "Center for Reading and Writing: Apply with the online form by March 1. [1]", [1])),
        "how do i become a peer tutor")
    assert HELP_TEXT not in plain.body["answer"]


def test_a_contact_question_naming_an_offices_service_label_is_looked_up_by_code_first() -> None:
    # "Who do I contact for help with Canvas?" names the Instructional Design Center by the
    # label its own page uses; code reads its contacts before any page search (11-03).
    service = meaning_service()
    library = next(e for e in service.entities if e["id"] == "library")
    library["aliases"] = ["Book Renewals"]
    for question in ("Who do I contact for help with Book Renewals?",
                     "How do I contact the Library?"):
        assert routes_here(question)
        gateway = PartsGateway()
        request = ChatRequest.model_validate({"messages": [
            {"role": "user", "content": question}]})
        engine = ChatEngine(gateway, service, output="written", howto_one_call=True)
        result = asyncio.run(engine.answer(intake(request, now=NOW), request))
        assert result.trace is not None and result.trace["decidedBy"] == "code", question
        assert gateway.inputs == [] and gateway.embedded == []
        assert "library@example.edu" in result.body["answer"]
    # A how-to naming the office still reads its pages.
    assert routes_here("How do I borrow books from the Library?")
