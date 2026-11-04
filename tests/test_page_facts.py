"""Page answers in two stages (page_facts.py): the facts first, then a writer."""

import asyncio
import json
from datetime import date
from typing import Any

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.page_facts import (
    agreed,
    asked_kinds,
    facts_of,
    misses,
    numbered,
    pick_messages,
    sentences,
    shape_of,
    template_text,
    tokens,
)
from rockygpt_brain.provider import Completion, ToolCall, Usage
from rockygpt_brain.turn import intake
from test_engine import NOW
from test_meaning import meaning_service
from test_one_call import PartsGateway

TODAY = date(2026, 11, 4)

PARKING: list[dict[str, Any]] = [{"office": "Public Safety", "sections": [
    {"n": 1, "heading": "Appeals", "page": "https://www.ramapo.edu/publicsafety/appeals/",
     "text": "An online appeal must be made within twenty (20) days of the issuance of a "
             "citation. Appeals will not be considered for: • Ignorance of the regulation "
             "• Inability to pay the fine. All decisions of the Appeals Board are FINAL."},
    {"n": 2, "heading": "Payment of Fines", "page": "https://www.ramapo.edu/publicsafety/pay/",
     "text": "During regular business hours, payments may be made to the Office of Student "
             "Accounts. Citations not paid or appealed within 20 days are subject to a $5 late "
             "fee. A second $5 late fee may be assessed after 28 days."}]},
    {"office": "Counseling Center", "sections": [
        {"n": 3, "heading": "How long does it take?", "page": "https://www.ramapo.edu/cc/",
         "text": "Anywhere from 10-30 minutes, depending on your health behaviors."}]}]


def pick(*numbers: int, asked: str = "the question") -> dict[str, Any]:
    return {"parts": [{"asked": asked, "not_on_pages": False, "sentences": list(numbers)}]}


def test_sections_become_sentences_and_a_list_stays_with_its_lead() -> None:
    said = sentences(PARKING[0]["sections"][0]["text"])
    assert said == [
        "An online appeal must be made within twenty (20) days of the issuance of a citation.",
        "Appeals will not be considered for: Ignorance of the regulation; Inability to pay the "
        "fine.",
        "All decisions of the Appeals Board are FINAL."]
    # "Dr.", "Rm." and "a.m." end no sentence; a label too short to say anything is read with
    # what follows it.
    assert sentences("Fall and Spring A student may take 18 credits. See Dr. Lee in Rm. C101 "
                     "by 9 a.m. Monday.") == [
        "Fall and Spring A student may take 18 credits.",
        "See Dr. Lee in Rm. C101 by 9 a.m. Monday."]


def test_each_sentence_is_marked_with_what_it_holds_and_the_question_with_what_it_asks() -> None:
    lines = numbered(PARKING)
    by_text = {line["says"][:20]: line["kinds"] for line in lines}
    assert "deadline" in by_text["An online appeal mus"]
    assert {"deadline", "amount"} <= set(by_text["Citations not paid o"])
    assert [line["source"] for line in lines] == [1, 1, 1, 2, 2, 2, 3]
    question = "i got a parking ticket. how do i pay or appeal it and how long do i have"
    assert asked_kinds(question) == ["deadline", "step", "link"]
    assert shape_of(question) == "value_then_steps"
    assert shape_of("can i still switch one of my classes to audit") == "yes_no"
    assert shape_of("how much are the meal plans this year") == "value"


def test_a_sentence_said_again_word_for_word_is_numbered_once() -> None:
    twice = [{"office": "Orientation", "sections": [
        {"n": 1, "heading": "Credits", "text": "To be full time, you must take at least 12 "
                                               "credits."},
        {"n": 2, "heading": "Credits", "text": "To be full time, you must take at least 12 "
                                               "credits."}]}]
    assert len(numbered(twice)) == 1


def test_the_sentences_most_picks_choose_are_kept_and_code_adds_the_kinds_asked() -> None:
    lines = numbered(PARKING)
    # Two of three picks choose the appeal deadline and the late fee; one also chose the
    # counseling sentence, which is not kept.
    kept = agreed([pick(1, 5), pick(5, 1, 7), pick(1)], len(lines))
    assert kept == [1, 5]
    assert agreed([None, None], len(lines)) is None
    page = facts_of("how long do i have to appeal a parking ticket", lines, kept, TODAY)
    # Every other deadline in the sections used is code's: the second late fee.
    assert [(f["says"][:22], f["chosen_by"]) for f in page["facts"]] == [
        ("An online appeal must ", "ai"), ("Citations not paid or ", "ai"),
        ("A second $5 late fee m", "code")]
    assert [f["n"] for f in page["facts"]] == [1, 2, 3]
    assert {f["office"] for f in page["facts"]} == {"Public Safety"}
    assert page["shape"] == "value_then_steps" and "capped" not in page


def test_more_facts_than_the_limit_keep_the_picked_ones_and_say_so() -> None:
    many = [{"office": "Registrar", "sections": [{"n": 1, "heading": "Dates", "text": " ".join(
        f"Deadline number {k} is due on November {k}, 2026." for k in range(1, 21))}]}]
    lines = numbered(many)
    picked = [1, 4, 7, 10, 13, 16, 19]
    page = facts_of("when are the deadlines", lines, picked, TODAY)
    # Every deadline is near a picked one; the picked ones are kept first.
    assert len(page["facts"]) == 14 and page["capped"] == 6
    said = {f["says"] for f in page["facts"]}
    assert all(f"Deadline number {k} is due on November {k}, 2026." in said for k in picked)
    # Code adds only sentences near a picked one: the winter limit far from the fall one is not.
    far = [{"office": "Provost", "sections": [{"n": 1, "heading": "Credit Load", "text": (
        "Fall and Spring students may take up to 18 credits. Others ask the Registrar. Plans "
        "vary by program. Advisors can help. Winter students may take up to 4 credits.")}]}]
    page = facts_of("whats the max credits", numbered(far), [1], TODAY)
    assert [f["says"][:10] for f in page["facts"]] == ["Fall and S"]


def test_the_template_says_each_offices_facts_in_the_pages_words_with_their_marks() -> None:
    lines = numbered(PARKING)
    page = facts_of("how do i pay a parking ticket", lines, [4, 5], TODAY)
    assert template_text(page) == (
        "**Public Safety**\nDuring regular business hours, payments may be made to the Office "
        "of Student Accounts. Citations not paid or appealed within 20 days are subject to a $5 "
        "late fee. [2]")
    assert template_text({"facts": []}) == ""


def test_the_pick_sees_numbered_sentences_under_their_office_and_heading() -> None:
    content = pick_messages("how do i appeal", None, numbered(PARKING))[1]["content"]
    assert "## Public Safety | Appeals\n1. An online appeal" in content
    assert "## Counseling Center | How long does it take?\n7. Anywhere" in content


def test_the_check_finds_facts_left_out_and_things_no_fact_holds() -> None:
    lines = numbered(PARKING)
    page = facts_of("how long do i have to appeal a parking ticket", lines, [1, 5], TODAY)
    good = ("Appeal online within 20 days of the citation [1]. If it is not paid or appealed "
            "within 20 days there is a $5 late fee [2], and a second $5 fee after 28 days [2].")
    assert misses(good, page) == {}
    short = "Appeal online within 20 days of the citation [1]."
    assert misses(short, page) == {"unsaid": [2, 3]}
    made_up = good + " Call (201) 684-1234 or email help@ramapo.edu; he will help."
    assert misses(made_up, page) == {"extra": ["2016841234", "help@ramapo.edu", "he or she"]}
    # Words for numbers count as their digits; marks and list numbers are not numbers said.
    assert {"20", "5"} <= tokens("twenty days, five dollars")
    assert tokens("1. Pay online [3].") == {"1"}
    assert misses("1. " + good, page) == {}


def staged(*arguments: dict[str, Any]) -> list[Completion]:
    return [Completion("", (ToolCall("p", "pick_facts", a),),
                       [{"type": "function_call", "call_id": "p", "name": "pick_facts",
                         "arguments": json.dumps(a)}], Usage(10, 2, 0)) for a in arguments]


def written(text: str) -> Completion:
    return Completion("", (ToolCall("w", "write_answer", {"text": text}),),
                      [{"type": "function_call", "call_id": "w", "name": "write_answer",
                        "arguments": json.dumps({"text": text})}], Usage(10, 10, 0))


def ask(gateway: Any, writer: str | None = None, page_writer: str = "template") -> Any:
    request = ChatRequest.model_validate({
        "messages": [{"role": "user", "content": "how do i become a peer tutor"}],
        **({"writer": writer} if writer else {})})
    engine = ChatEngine(gateway, meaning_service(), output="written", howto_one_call=True,
                        page_writer=page_writer)
    return asyncio.run(engine.answer(intake(request, now=NOW), request))


def test_with_the_template_writer_the_picked_facts_are_said_in_the_pages_words() -> None:
    gateway = PartsGateway(*staged(pick(1), pick(1), pick(1)))
    result = ask(gateway)
    text = result.body["answer"]
    assert text.startswith("**Center for Reading and Writing**\nApply to become a peer tutor "
                           "with the online form by March 1. [1]")
    assert "Sources: [1](https://www.ramapo.edu/crw/tutor/)" in text
    page = result.body["pageFacts"]
    assert page["writer"] == "template" and page["fell_back"] is False
    assert [f["says"] for f in page["facts"]] == [
        "Apply to become a peer tutor with the online form by March 1."]
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"
    assert len(gateway.inputs) == 3  # Three sentence picks, no writer call.


def test_the_ai_writer_says_only_the_facts_and_falls_back_to_the_template() -> None:
    good = written("To become a peer tutor, apply with the online form by March 1. [1]")
    result = ask(PartsGateway(*staged(pick(1), pick(1), pick(1)), good), page_writer="ai")
    assert result.body["answer"].startswith("To become a peer tutor, apply with the online form "
                                            "by March 1. [1]")
    assert result.body["pageFacts"]["writer"] == "ai"
    assert result.body["pageFacts"]["fell_back"] is False
    assert result.body["pageFacts"]["tries"] == 1
    # A date no fact holds, twice: the template's words go out instead.
    wrong = written("Apply with the online form by March 15. [1]")
    gateway = PartsGateway(*staged(pick(1), pick(1), pick(1)), wrong, wrong)
    result = ask(gateway, page_writer="ai")
    assert "by March 1. [1]" in result.body["answer"]
    assert result.body["pageFacts"]["fell_back"] is True
    assert result.body["pageFacts"]["misses"] == {"unsaid": [1], "extra": ["15"]}
    assert "It said these, which no fact holds: 15." in gateway.inputs[-1][-1]["content"]


def test_a_request_can_ask_for_either_writer_whatever_the_setting() -> None:
    good = written("Apply with the online form by March 1. [1]")
    result = ask(PartsGateway(*staged(pick(1), pick(1), pick(1)), good), writer="ai",
                 page_writer="one_call")
    assert result.body["pageFacts"]["writer"] == "ai"
    result = ask(PartsGateway(*staged(pick(1), pick(1), pick(1))), writer="template",
                 page_writer="ai")
    assert result.body["pageFacts"]["writer"] == "template"


def test_no_sentence_picked_leaves_the_answer_to_the_one_call() -> None:
    from test_one_call import one_reply
    gateway = PartsGateway(*staged(pick(), pick(), pick()), one_reply(
        "Apply to become a peer tutor with the online form by March 1. [1]", [1]))
    result = ask(gateway)
    assert "March 1. [1]" in result.body["answer"] and "pageFacts" not in result.body
    assert result.trace is not None and result.trace["pageFacts"]["picked"] == 0
