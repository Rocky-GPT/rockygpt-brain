"""The writer's verifier and its place in a turn: it may only say what the writer view holds."""

import asyncio
import json
from typing import Any

import pytest

from rockygpt_brain.answers import plain_links
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.provider import Completion, GatewayError, ToolCall, Usage
from rockygpt_brain.turn import intake
from rockygpt_brain.writer import says_something, unsupported_by
from test_engine import LOOKUP, NOW, ScriptedGateway, completion, facts, finish

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
        {"messages": [{"role": "user",
                       "content": "What is the Registrar email for a transcript?"}]})
    engine = ChatEngine(ScriptedGateway(*steps), facts(), output=output,  # type: ignore[arg-type]
                        lookup_writer=True)
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


def test_a_lookup_answer_is_written_by_code_unless_the_model_writer_is_turned_on() -> None:
    # Dan 11-02: lookup answers are code's plain sentences, the same every time, with no AI call.
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "What is the Registrar email?"}]})
    gateway = ScriptedGateway()
    engine = ChatEngine(gateway, facts(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert result.body["answer"] == ("Registrar email: published@example.edu. [1]\n\n"
                                     "Sources: [1](https://example.edu/registrar)")
    assert result.trace is not None and result.trace["writer"] == {"used": True, "by": "code"}
    # A plain lookup of a listed office is routed by code too: no model call at all.
    assert gateway.inputs == [] and result.trace["decidedBy"] == "code"


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
            facts(), output="written", lookup_writer=True)
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


def test_a_turn_that_used_every_call_still_gets_its_writer() -> None:
    from rockygpt_brain.provider import TurnBudget

    class Counting(ScriptedGateway):
        """Counts calls against the turn's limit, as the real gateway does."""

        async def complete(self, *, input: list[dict[str, Any]], tools: list[dict[str, Any]],
                           budget: TurnBudget) -> Completion:
            if budget.calls >= budget.max_calls:
                raise GatewayError("turn_limit")
            budget.calls += 1
            return await super().complete(input=input, tools=tools, budget=budget)

    request = ChatRequest.model_validate(
        {"messages": [{"role": "user",
                       "content": "What is the Registrar email for a transcript?"}]})
    # Four lookup rounds and the finish use all five calls; the writer is a sixth.
    steps = [LOOKUP, LOOKUP, LOOKUP, LOOKUP, finish(),
             writing("The Registrar's email is published@example.edu [1].")]
    engine = ChatEngine(Counting(*steps), facts(), output="written", lookup_writer=True)
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert result.trace is not None and result.trace["writer"] == {"used": True}


def test_the_writer_is_told_what_the_student_wants_for_a_how_to() -> None:
    from test_engine import registrar_with_pages
    ask = Completion("", (ToolCall("c", "graph_lookup", {"requests": [
        {"query": "Registrar", "fields": ["procedures"], "about": "request a transcript"}]}),),
        [{"type": "function_call", "call_id": "c", "name": "graph_lookup", "arguments": json.dumps(
            {"requests": [{"query": "Registrar", "fields": ["procedures"],
                           "about": "request a transcript"}]})}], Usage(1, 1, 0))
    pick = Completion("", (ToolCall("p", "pick_sections", {"picks": [{"lookup": 0,
                                                                      "sections": [0]}]}),),
                      [], Usage(1, 1, 0))
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "how do i get my transcript"}]})
    engine = ChatEngine(ScriptedGateway(ask, pick, finish()), registrar_with_pages(),
                        output="json")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert result.body["writerInput"]["asked"]["wants"] == ["request a transcript"]


def test_an_emergency_beside_asked_facts_keeps_its_text_first_and_writes_the_facts_after() -> None:
    from rockygpt_brain.boundary import SAFETY_MESSAGE
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "registrar email, not 911 obviously lol"}]})
    engine = ChatEngine(ScriptedGateway(
        LOOKUP, finish(), writing("The Registrar's email is published@example.edu [1].")),
        facts(), output="written", lookup_writer=True)
    result = asyncio.run(engine.answer(intake(request, now=NOW), request, floor_situation="other"))
    text = result.body["answer"]
    # Dan 10-20: crisis first, then the facts, in the writer's words, said once.
    assert text.startswith(SAFETY_MESSAGE)
    assert "The Registrar's email is published@example.edu [1]." in text
    assert text.count(SAFETY_MESSAGE) == 1
    assert result.trace is not None and result.trace["writer"] == {"used": True}


def test_a_number_the_pages_spell_out_and_a_phone_with_unicode_dashes_are_held() -> None:
    view = {"offices": [{"sections": [{"n": 1, "text": "Appeal within twenty calendar days, "
                                                      "or call (201) 684-7495."}]}],
            "sources": [{"n": 1, "urls": ["https://www.ramapo.edu/x/"]}]}
    assert unsupported_by("Appeal within 20 calendar days or call (201) 684\u20117495. [1]",
                          view) == []
    assert unsupported_by("Appeal within 21 days. [1]", view) == [
        "a number the facts do not hold: 21"]


def test_a_long_reply_in_codes_words_is_never_cut_or_left_to_the_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 11-03 practice: code's long reply was taken for a cut-off model reply, and the student read
    # the evidence text instead, internal review notes and all. Code's sentences end where they
    # end; plain_text fits a long reply by quoting its pages shorter.
    import rockygpt_brain.engine as engine_module

    said = "Registrar email: published@example.edu. [1] " * 120
    monkeypatch.setattr(engine_module, "plain_text", lambda view, limit=None: said)
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "What is the Registrar email?"}]})
    engine = ChatEngine(ScriptedGateway(), facts(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert result.trace is not None and result.trace["writer"] == {"used": True, "by": "code"}
    assert len(said) > 4000 and result.body["answer"].startswith(said.strip())


def test_a_lookup_that_said_what_it_lacks_is_not_followed_by_saying_it_again() -> None:
    # 11-03 practice: "where is the bradley center" said "I don't have verified information"
    # twice in a row, the lookup's own line and then the refusal's.
    from rockygpt_brain.engine import UNSUPPORTED_GAP_SAID_MESSAGE

    room = completion("graph_lookup", {"requests": [{"query": "Registrar",
                                                     "fields": ["offices"]}]})
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "where does the registrar sit"}]})
    engine = ChatEngine(ScriptedGateway(room, finish("unsupported")), facts(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert result.body["answer"] == ("I don't have verified information about Registrar's "
                                     f"location.\n\n{UNSUPPORTED_GAP_SAID_MESSAGE}")


@pytest.mark.parametrize("said,shown", [
    # The sentence's punctuation is not part of the address (11-03 repeat test).
    ("See https://example.edu/registrar/, then wait.",
     "See [https://example.edu/registrar/](https://example.edu/registrar/), then wait."),
    ("Apply at https://example.edu/registrar.",
     "Apply at [https://example.edu/registrar](https://example.edu/registrar)."),
    ("Transport: https://example.edu/registrar; seats are first come.",
     "Transport: [https://example.edu/registrar](https://example.edu/registrar); seats are "
     "first come."),
    # An address inside escaped brackets, as the evidence text writes it.
    ("The page \\(https://example.edu/registrar/\\) lists it.",
     "The page \\([https://example.edu/registrar/](https://example.edu/registrar/)\\) lists it."),
    # An address already written as a link, and its label, are left as they are.
    ("Read [the page](https://example.edu/registrar/), then wait.",
     "Read [the page](https://example.edu/registrar/), then wait."),
    ("Sources: [1](https://example.edu/registrar)", "Sources: [1](https://example.edu/registrar)"),
])
def test_an_address_written_on_its_own_becomes_a_link(said: str, shown: str) -> None:
    assert plain_links(said) == shown


def test_the_reply_a_student_reads_has_no_address_ending_in_punctuation() -> None:
    body, _ = turn(LOOKUP, finish(),
                   writing("Email published@example.edu or read "
                           "https://example.edu/registrar, which lists the steps [1]."))
    assert "https://example.edu/registrar," not in body["answer"]
    assert "[https://example.edu/registrar](https://example.edu/registrar)," in body["answer"]
