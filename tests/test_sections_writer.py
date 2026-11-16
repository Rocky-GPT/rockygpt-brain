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
