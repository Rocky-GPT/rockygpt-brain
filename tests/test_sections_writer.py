"""The "sections" page writer (plan "one simple page writer", step 3): one call writes the answer
from whole plain sections the section search found, with today's date, citing each fact; code
then drops what no section holds, as for every one-call answer. No sentence picks, no template."""

import asyncio
from typing import Any

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.turn import intake
from test_engine import NOW
from test_meaning import meaning_service
from test_one_call import PartsGateway, one_reply

TUTOR = "Apply to become a peer tutor with the online form by March 1. [1]"


def ask(gateway: Any, *, setting: str, writer: str | None = None) -> Any:
    request = ChatRequest.model_validate({
        "messages": [{"role": "user", "content": "how do i become a peer tutor"}],
        **({"writer": writer} if writer else {})})
    engine = ChatEngine(gateway, meaning_service(), output="written", howto_one_call=True,
                        page_writer=setting)
    return asyncio.run(engine.answer(intake(request, now=NOW), request))


def test_one_call_writes_the_answer_from_the_sections_found_with_todays_date() -> None:
    gateway = PartsGateway(one_reply(TUTOR, [1]))
    result = ask(gateway, setting="sections")
    assert "by March 1. [1]" in result.body["answer"] and "pageFacts" not in result.body
    # One model call: the writer's. No section pick, no sentence picks.
    assert len(gateway.inputs) == 1
    said = " ".join(str(m.get("content")) for m in gateway.inputs[0])
    assert NOW.date().isoformat() in said or NOW.strftime("%B") in said
    assert result.trace is not None and result.trace["pageWriter"] == "sections"
    assert result.trace["decidedBy"] == "one_call"
    assert result.trace["oneCallRead"]


def test_a_request_can_ask_for_the_sections_writer_whatever_the_setting() -> None:
    gateway = PartsGateway(one_reply(TUTOR, [1]))
    result = ask(gateway, setting="one_call", writer="sections")
    assert "by March 1. [1]" in result.body["answer"]
    assert result.trace is not None and result.trace["pageWriter"] == "sections"
    assert len(gateway.inputs) == 1


def test_a_number_no_section_holds_is_dropped_as_for_every_one_call_answer() -> None:
    made_up = TUTOR + " Call (201) 684-9999 for help. [1]"
    result = ask(PartsGateway(one_reply(made_up, [1])), setting="sections")
    assert "684-9999" not in result.body["answer"] and "March 1" in result.body["answer"]


def test_the_sections_writer_gives_the_newer_page_alone_and_never_a_page_date() -> None:
    # 11-16: answers on adding a parent as an authorized user said a page was "older, updated
    # March 2026"; the student did not ask, and graders read it as a claim the pages do not make.
    from datetime import date

    from rockygpt_brain import one_call
    sections_rules = one_call.SECTIONS_PROMPT
    assert "do not mention the older page" in sections_rules
    assert "never say a page's date" in sections_rules
    assert "with its page_updated month and year" not in sections_rules
    # Today's writers keep their own rules.
    assert "with its page_updated month and year" in one_call.ONE_CALL_PROMPT
    sent = one_call.messages("q", None, [], date(2026, 11, 16), prompt=sections_rules)
    assert sent[0]["content"] == sections_rules


def direct_reply(text: str) -> Any:
    """What the direct writer's model call returns: the reply alone."""
    import json

    from rockygpt_brain.provider import Completion, ToolCall, Usage
    arguments = {"text": text}
    return Completion("", (ToolCall("w", "write_answer", arguments),),
                      [{"type": "function_call", "call_id": "w", "name": "write_answer",
                        "arguments": json.dumps(arguments)}], Usage(10, 10, 0))


class ToolsSeen(PartsGateway):
    """Also keeps the tools each call offered."""

    def __init__(self, *steps: Any) -> None:
        super().__init__(*steps)
        self.tools: list[list[dict[str, Any]]] = []

    async def complete(self, *, input: list[dict[str, Any]], tools: list[dict[str, Any]],
                       budget: Any) -> Any:
        self.tools.append(tools)
        return await super().complete(input=input, tools=tools, budget=budget)


def test_the_direct_writer_reads_the_same_sections_and_writes_the_reply_in_one_call() -> None:
    # Audit plan item 6 (11-17): the short fixed prompt, the reply alone, the campus time given.
    import json

    from rockygpt_brain import one_call
    gateway = ToolsSeen(direct_reply(TUTOR))
    result = ask(gateway, setting="direct")
    assert "by March 1. [1]" in result.body["answer"]
    assert len(gateway.inputs) == 1
    assert gateway.inputs[0][0]["content"] == one_call.DIRECT_PROMPT
    assert gateway.tools[0] == [one_call.DIRECT_TOOL]
    given = json.loads(gateway.inputs[0][1]["content"])
    # Campus (New York) time, not the server's.
    assert given["now"] == "8:00 AM" and given["offices"]
    assert result.trace is not None and result.trace["pageWriter"] == "direct"
    assert result.trace["decidedBy"] == "one_call" and result.trace["oneCallRead"]


def test_the_direct_writer_makes_no_second_look_and_keeps_the_every_word_check() -> None:
    gave_up = "I don't have verified information about the tutor pay. [1]"
    gateway = ToolsSeen(direct_reply(TUTOR + " " + gave_up))
    result = ask(gateway, setting="direct")
    assert len(gateway.inputs) == 1 and "oneCallSecondLook" not in (result.trace or {})
    made_up = TUTOR + " Call (201) 684-9999 for help. [1]"
    result = ask(ToolsSeen(direct_reply(made_up)), setting="direct")
    assert "684-9999" not in result.body["answer"] and "March 1" in result.body["answer"]


def test_a_request_can_ask_for_the_direct_writer_and_the_prompt_never_changes() -> None:
    from datetime import date, datetime

    from rockygpt_brain import one_call
    gateway = ToolsSeen(direct_reply(TUTOR))
    result = ask(gateway, setting="sections", writer="direct")
    assert result.trace is not None and result.trace["pageWriter"] == "direct"
    first = one_call.messages("q", None, [], date(2026, 11, 17), prompt=one_call.DIRECT_PROMPT,
                              now=datetime(2026, 11, 17, 9, 5))
    later = one_call.messages("other", None, [], date(2026, 12, 1),
                              prompt=one_call.DIRECT_PROMPT, now=datetime(2026, 12, 1, 22, 40))
    assert first[0] == later[0] and "9:05 AM" in first[1]["content"]


def ask_read_first(gateway: Any, message: str) -> Any:
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": message}]})
    engine = ChatEngine(gateway, meaning_service(), output="written", howto_one_call=True,
                        page_writer="direct", read_first=True, reads=1)
    return asyncio.run(engine.answer(intake(request, now=NOW), request))


def test_the_direct_writer_embeds_the_students_words_while_the_message_is_read() -> None:
    # Audit item 6 (11-17): the embedding waited for the read (1.2 s); now it runs beside it and
    # the search uses it when the read keeps the student's words.
    from test_triage import read
    gateway = ToolsSeen(read(), direct_reply(TUTOR))
    result = ask_read_first(gateway, "how do i become a peer tutor")
    assert "by March 1. [1]" in result.body["answer"]
    assert gateway.embedded == [["how do i become a peer tutor"]]
    assert result.trace is not None and result.trace["pageWriter"] == "direct"


def test_the_search_embeds_again_when_the_read_asks_in_its_own_words() -> None:
    from test_triage import read
    gateway = ToolsSeen(read(question="How do I become a peer tutor?"), direct_reply(TUTOR))
    result = ask_read_first(gateway, "how do i becom a peer tutr")
    assert "by March 1. [1]" in result.body["answer"]
    assert gateway.embedded[0] == ["how do i becom a peer tutr"]
    assert gateway.embedded[-1] == ["How do I become a peer tutor?"]


def test_a_failed_early_embedding_leaves_the_search_its_own() -> None:
    from rockygpt_brain.provider import GatewayError
    from test_triage import read

    class FailsFirst(ToolsSeen):
        async def embed_many(self, texts: Any, *, dimensions: int, budget: Any) -> Any:
            if not self.embedded:
                self.embedded.append(["failed"])
                raise GatewayError("provider_unavailable")
            return await super().embed_many(texts, dimensions=dimensions, budget=budget)

    gateway = FailsFirst(read(), direct_reply(TUTOR))
    result = ask_read_first(gateway, "how do i become a peer tutor")
    assert "by March 1. [1]" in result.body["answer"]
    assert gateway.embedded == [["failed"], ["how do i become a peer tutor"]]
