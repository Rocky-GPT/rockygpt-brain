"""The writer's verifier and its place in a turn: it may only say what the writer view holds."""

import asyncio
import json
from typing import Any

import pytest

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.provider import Completion, GatewayError, ToolCall, Usage
from rockygpt_brain.turn import intake
from rockygpt_brain.writer import says_something, unsupported_by
from test_engine import LOOKUP, NOW, ScriptedGateway, facts, finish

VIEW: dict[str, Any] = {
    "asked": {"intent": "contact_and_hours", "offices": ["Library"], "fields": ["hours", "email"]},
    "status": "complete",
    "facts": [
        {"subject": "Library", "predicate": "email", "value": "library@ramapo.edu", "sources": [1]},
        {"subject": "Library", "predicate": "phones",
         "value": [{"number": "+12016847000"}], "sources": [1]},
    ],
    "derived_facts": [{"subject": "Library", "predicate": "hours_on", "day": "Saturday",
                       "date": "2026-10-17", "value": {"hours": "10:00am-6:00pm"}, "sources": [2]}],
    "not_published": [{"subject": "Library", "predicate": "fax", "checked_at": "2026-10-07"}],
    "sources": [{"n": 1, "title": "campus-directory", "urls": ["https://www.ramapo.edu/library/"]},
                {"n": 2, "title": "campus-hours",
                 "urls": ["https://www.ramapo.edu/library/library-hours/"]}],
}


@pytest.mark.parametrize("text", [
    "The Library is open 10:00am-6:00pm on Saturday, October 17 [2].",
    "Email library@ramapo.edu or call (201) 684-7000 [1].",
    "Their number is +12016847000 and 201.684.7000 works the same way.",
    "More at https://www.ramapo.edu/library/library-hours/.",
    "It opens at 10 and closes at 6.",
    "The page was checked on October 7, 2026 (the facts hold 2026-10-07).",
])
def test_what_the_view_holds_passes_in_any_punctuation(text: str) -> None:
    assert unsupported_by(text, VIEW) == []


@pytest.mark.parametrize("text,what", [
    ("Email library@example.com for help.", "email address"),
    ("Call (201) 555-0199 any time.", "phone number"),
    ("Closes at 9:30pm on Saturday.", "number"),
    ("See https://www.ramapo.edu/not-a-page/ for more.", "web address"),
    ("The room is C-102 [3].", "number"),
])
def test_what_the_view_does_not_hold_is_refused(text: str, what: str) -> None:
    problems = unsupported_by(text, VIEW)
    assert problems and any(what in problem for problem in problems)


def test_a_view_with_only_a_question_or_fixed_wording_has_nothing_to_write() -> None:
    assert says_something(VIEW)
    assert not says_something({"asked": {}, "status": "ambiguous",
                               "ambiguities": [{"query": "x", "choices": ["A", "B"]}]})
    assert not says_something({"asked": {}, "status": "emergency",
                               "notices": [{"approved_text": "Call 911."}]})


def writing(text: str | None) -> Completion:
    if text is None:
        return Completion("", (), [], Usage(1, 1, 0))
    arguments = {"text": text}
    return Completion("", (ToolCall("w", "write_answer", arguments),),
                      [{"type": "function_call", "call_id": "w", "name": "write_answer",
                        "arguments": json.dumps(arguments)}], Usage(10, 10, 0))


def turn(*steps: Completion, output: str = "written") -> tuple[dict[str, Any], dict[str, Any]]:
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "What is the Registrar email?"}]})
    engine = ChatEngine(ScriptedGateway(*steps), facts(), output=output)  # type: ignore[arg-type]
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert result.trace is not None
    return result.body, result.trace


def test_a_verified_reply_is_what_the_student_reads_with_its_sources() -> None:
    body, trace = turn(
        LOOKUP, finish(), writing("The Registrar's email is published@example.edu [1]."))
    assert body["answer"].startswith("The Registrar's email is published@example.edu [1].")
    assert "Sources: [1](https://example.edu/registrar)" in body["answer"]
    assert trace["writer"] == {"used": True}
    assert body["facts"]["status"] == "complete" and body["writerInput"]["facts"]


@pytest.mark.parametrize("step,why", [
    (writing("The Registrar's email is wrong@example.edu."), "not_in_the_facts"),
    (writing(None), "no_text"),
    (writing("   "), "no_text"),
    # Strict output stops a long reply at the length limit, mid-word: it is never shown.
    (writing("The Registrar's email is published@example.edu. " * 100), "cut_off"),
])
def test_a_reply_the_view_does_not_hold_is_replaced_by_the_codes_text(
    step: Completion, why: str,
) -> None:
    body, trace = turn(LOOKUP, finish(), step)
    assert trace["writer"]["used"] is False and trace["writer"]["why"] == why
    assert "published@example.edu" in body["answer"]
    assert "wrong@example.edu" not in body["answer"]


def test_a_writer_that_fails_leaves_the_codes_text() -> None:
    def broken() -> Completion:
        raise GatewayError("provider_unavailable")

    body, trace = turn(LOOKUP, finish(), broken)  # type: ignore[arg-type]
    assert trace["writer"] == {"used": False, "why": "writer_unavailable",
                               "error": "provider_unavailable"}
    assert "published@example.edu" in body["answer"]


def test_the_writer_is_not_asked_when_the_turn_is_not_a_written_one() -> None:
    body, trace = turn(LOOKUP, finish(), output="json")
    assert "writer" not in trace and body["answer"] == ""
    text_body, text_trace = turn(LOOKUP, finish(), output="text")
    assert "writer" not in text_trace and "published@example.edu" in text_body["answer"]


def test_the_writers_own_prompt_holds_no_real_number_it_could_copy() -> None:
    import re

    from rockygpt_brain.writer import WRITER_PROMPT
    # An example number in the prompt was once copied into an answer as if it were the office's.
    assert not re.search(r"\d{3}\D{0,3}\d{3}\D{0,3}\d{4}", WRITER_PROMPT)
    assert "@" not in WRITER_PROMPT and "http" not in WRITER_PROMPT


def test_written_output_never_returns_an_empty_answer_on_the_code_only_paths() -> None:
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "my friend is not breathing"}]})
    engine = ChatEngine(ScriptedGateway(), facts(), output="written")
    # The phrase floor's reply (no model call at all) carries the emergency words as the answer.
    floor = asyncio.run(engine.safety_reply(intake(request, now=NOW), "medical"))
    assert floor.body["answer"].strip()
    assert floor.body["facts"]["status"] == "emergency"
    # A finish with nothing in it is the generic question, in words.
    body, trace = turn(finish(), output="written")
    assert body["answer"].strip() and "writer" not in trace


def test_a_fixed_notice_the_writer_already_said_is_not_shown_twice() -> None:
    from rockygpt_brain.engine import UNSUPPORTED_AFTER_LOOKUP_MESSAGE

    def run(said: str) -> str:
        request = ChatRequest.model_validate({"messages": [{
            "role": "user",
            "content": "What is the Registrar email and can I book an appointment?"}]})
        engine = ChatEngine(
            ScriptedGateway(LOOKUP, finish("unsupported", student_words="book an appointment"),
                            writing(said)),
            facts(), output="written")  # type: ignore[arg-type]
        result = asyncio.run(engine.answer(intake(request, now=NOW), request))
        return str(result.body["answer"])

    notice = UNSUPPORTED_AFTER_LOOKUP_MESSAGE
    repeated = run(f"The Registrar's email is published@example.edu [1]. {notice}")
    assert repeated.count("I don't have verified information") == 1
    # A reply that leaves it out still carries it, once.
    left_out = run("The Registrar's email is published@example.edu [1].")
    assert left_out.count(notice) == 1


def test_a_reply_that_drops_every_departure_time_is_not_used() -> None:
    from rockygpt_brain.engine import left_out_departures
    view = {"derived_facts": [{"predicate": "departures", "value": {"trips": [
        {"leaves_campus": "11:35 AM"}, {"leaves_campus": "11:45 AM"}]}}]}
    assert left_out_departures("Health Services is in D-102.", view)
    assert not left_out_departures("The next shuttle leaves at 11:35am.", view)
    assert not left_out_departures("Health Services is in D-102.", {"derived_facts": []})


def test_a_numbered_step_is_the_replys_own_list_not_a_fact() -> None:
    from rockygpt_brain.writer import unsupported_by
    view = {"facts": [{"subject": "Registrar", "predicate": "procedures",
                       "value": {"heading": "Transcripts", "text": "Submit the form. Pay $10."}}],
            "sources": [{"n": 1}]}
    assert unsupported_by("Registrar:\n1. Submit the form.\n2. Pay $10. [1]", view) == []
    # A number inside a sentence is still checked.
    assert unsupported_by("It takes 3 days.", view) == ["a number the facts do not hold: 3"]


def test_a_citation_mark_after_a_web_address_is_not_part_of_it() -> None:
    view = {"facts": [{"value": "https://www.ramapo.edu/undergraduate/transfer/faqs/"}],
            "sources": [{"n": 1}]}
    text = "See https://www.ramapo.edu/undergraduate/transfer/faqs/.[1]"
    assert unsupported_by(text, view) == []
