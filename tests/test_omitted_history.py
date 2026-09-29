"""A client that sends only the latest messages says how many it left out (09-29).

Both clients cut history at 40 messages. Unmarked, Q29's window lacked the 7:00 a.m.
answer from Q4, and the Brain said "I didn't give you a departure time earlier".
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rockygpt_brain.api.app import app
from rockygpt_brain.contracts import ChatMessage, ChatRequest
from rockygpt_brain.core.engine import DROPPED_NOTE, HISTORY_NOTE, run_turn
from test_api import deployment_environment, gateway_context  # noqa: F401
from test_engine import NOW, answer, review

QUESTION = {"role": "user", "content": "What time did you say the first shuttle was?"}


def test_the_request_says_how_many_messages_were_left_out() -> None:
    assert ChatRequest.model_validate({"messages": [QUESTION]}).omittedMessages == 0
    request = ChatRequest.model_validate({"messages": [QUESTION], "omittedMessages": 18})
    assert request.omittedMessages == 18
    for bad in ({"omittedMessages": -1}, {"omittedMessages": 100001}, {"omitted": 18}):
        with pytest.raises(ValidationError):
            ChatRequest.model_validate({"messages": [QUESTION], **bad})


def turn(omitted: int) -> Mock:
    client = Mock()
    client.create.side_effect = [answer("I can't see that part of our conversation."), review()]
    run_turn([ChatMessage.model_validate(QUESTION)], client=client, data=Mock(), model="test",
             now=NOW, omitted_messages=omitted)
    return client


def developer_context(client: Mock) -> str:
    [developer] = [item for item in client.create.call_args_list[0].kwargs["input"]
                   if isinstance(item, dict) and item.get("role") == "developer"]
    return str(developer["content"])


def test_the_writer_is_told_only_when_messages_were_left_out() -> None:
    told = developer_context(turn(18))
    assert ("The client sent only the latest 1 messages; 18 earlier messages are not shown."
            in told)
    assert "say you can't see that part of the conversation" in told
    # Nothing changes for a complete conversation, so the cached prompt stays the same.
    whole = developer_context(turn(0))
    assert "not shown" not in whole
    assert told.startswith(whole.rstrip("\n"))


def test_the_reviewer_is_told_how_many_messages_it_cannot_see() -> None:
    for omitted in (0, 18):
        client = turn(omitted)
        review_input = json.loads(client.create.call_args_list[1].kwargs["input"])
        assert review_input["earlier_messages_omitted"] == omitted
        instructions = client.create.call_args_list[1].kwargs["instructions"]
        assert "full\nconversation" not in instructions
        assert "earlier_messages_omitted" in instructions


@pytest.mark.parametrize("omitted,kept", [(18, False), (0, True)])
def test_a_denial_of_something_said_earlier_is_dropped_when_that_part_was_cut(
    omitted: int, kept: bool
) -> None:
    # 09-29 replay: with 18 earlier messages cut, "I didn't give you a first departure
    # time" passed the checker although the 7:00 a.m. answer was among the cut messages.
    client = Mock()
    client.create.side_effect = [
        answer("I didn't give you a first departure time earlier."),
        review(denies_earlier_message=True),
    ]
    result = run_turn([ChatMessage.model_validate(QUESTION)], client=client, data=Mock(),
                      model="test", now=NOW, omitted_messages=omitted)
    if kept:
        # With the whole conversation there, a denial is judged like any other part.
        assert result["answer"] == "I didn't give you a first departure time earlier."
    else:
        assert "didn't give you" not in result["answer"]
        assert result["metrics"]["validationFailures"] == ["wrong_context"]
        # What is true instead, written by code: not the generic "couldn't verify".
        assert result["answer"] == HISTORY_NOTE.text
        assert result["status"] == "unavailable"


def test_a_denial_beside_a_kept_part_is_replaced_by_the_history_note() -> None:
    client = Mock()
    client.create.side_effect = [
        SimpleNamespace(status="completed", model="test-model", output=[], output_text=json.dumps({
            "status": "answered", "parts": [
                {"kind": "guidance", "text": "I didn't give you a time earlier.",
                 "evidence_ids": []},
                {"kind": "guidance", "text": "Ask again and I'll look up today's timetable.",
                 "evidence_ids": []},
            ]})),
        SimpleNamespace(status="completed", model="test-model", output=[], output_text=json.dumps({
            "parts": [{"part_index": index, "verdict": "supported", "reason": "",
                       "unverified_premises": [], "uses_event_for_entity": False,
                       "infers_food_safety": False, "denies_earlier_message": index == 0,
                       "depends_on_parts": [], "plan_deadlines": []} for index in (0, 1)]})),
    ]
    result = run_turn([ChatMessage.model_validate(QUESTION)], client=client, data=Mock(),
                      model="test", now=NOW, omitted_messages=18)
    assert result["answer"] == (
        "Ask again and I'll look up today's timetable.\n\n" + HISTORY_NOTE.text)
    assert result["status"] == "partial" and result["metrics"]["historyNote"] is True
    # Nothing else was left out, so the generic note isn't added.
    assert DROPPED_NOTE.text not in result["answer"]


def test_the_api_passes_the_count_to_the_turn() -> None:
    seen: list[int] = []

    def result(*args: Any, **kwargs: Any) -> dict[str, object]:
        seen.append(kwargs["omitted_messages"])
        return {"answer": "Hello", "status": "answered", "metrics": {}}

    with (
        patch.dict("os.environ", {"STAGING_SERVICE_TOKEN": ""}),
        patch("rockygpt_brain.api.app.open_gateway", return_value=gateway_context()),
        patch("rockygpt_brain.api.app.CampusData"),
        patch("rockygpt_brain.api.app.run_turn", side_effect=result),
    ):
        client = TestClient(app)
        assert client.post("/v1/chat", json={"messages": [QUESTION],
                                             "omittedMessages": 18}).status_code == 200
        assert client.post("/v1/chat", json={"messages": [QUESTION]}).status_code == 200
        assert client.post("/v1/chat", json={"messages": [QUESTION],
                                             "omittedMessages": -1}).status_code == 422
    assert seen == [18, 0]
