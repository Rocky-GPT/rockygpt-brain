"""A how-to question answered by one model call from code-found evidence (one_call.py)."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.one_call import parts_of, routes_here, search_text
from rockygpt_brain.provider import Completion, ToolCall, TurnBudget, Usage
from rockygpt_brain.turn import intake
from test_engine import NOW, ScriptedGateway, completion, finish
from test_meaning import index_of, meaning_service
from test_writer import writing


def one_reply(text: str, used: list[int], facts: list[str] | None = None) -> Completion:
    found = ["Apply with the online form [1]"] if facts is None else facts
    arguments = {"parts": [{"asked": "how to become a tutor", "sections": used, "facts": found}],
                 "text": text, "used": used}
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
    engine = ChatEngine(gateway, meaning_service(), output="written",  # type: ignore[arg-type]
                        howto_one_call=True)
    return asyncio.run(engine.answer(intake(request, now=NOW), request))


def test_a_how_to_is_routed_here_and_menus_shuttles_and_plain_contacts_are_not() -> None:
    assert routes_here("how do i become a peer tutor")
    assert routes_here("can i get a refund on my meal plan if i withdraw")
    assert not routes_here("what's for lunch at the atrium")
    assert not routes_here("when is the next shuttle to the train station")
    assert not routes_here("registrar email")
    assert not routes_here("library hours")
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


def test_a_part_with_no_facts_gets_a_second_look_and_one_more_reply() -> None:
    gateway = PartsGateway(
        one_reply("I don't have verified information about that.", [], facts=[]),
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


def test_the_offices_whose_cards_match_are_read_for_their_contacts() -> None:
    service = meaning_service()
    units = [("page-advising", "0", ["advising"], [[0, 1, 0]]),
             ("page-tutoring", "0", ["tutoring"], [[1, 0, 0]])]
    service.index = index_of(units, service.index.names,
                             cards=[("library", [1, 0, 0]), ("advising", [0, 1, 0])])
    gateway = PartsGateway(one_reply("Apply with the online form by March 1. [1]", [1]))
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "how do i become a peer tutor"}]})
    engine = ChatEngine(gateway, service, output="written",  # type: ignore[arg-type]
                        howto_one_call=True)
    asyncio.run(engine.answer(intake(request, now=NOW), request))
    given = json.loads(gateway.inputs[0][1]["content"])["offices"]
    # The library has no section the search chose, but its card matches: its contacts are given.
    (library,) = [o for o in given if o["office"] == "Library"]
    assert library["sections"] == [] and library["email"]
