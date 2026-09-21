"""Follow-up selection and clarification retain independent evidence review."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from test_engine import answer, review, tools
from test_profile_sections import PERSON, person_data
from test_profiles import NOW


def profile_call(arguments: dict[str, object]) -> SimpleNamespace:
    return tools(SimpleNamespace(
        type="function_call", name="lookup_profile", call_id="profile-followup",
        arguments=json.dumps(arguments),
    ))


def test_contact_only_followup_retrieves_person_and_preserves_conversation_for_review() -> None:
    data = person_data()
    client = Mock()
    client.create.side_effect = [
        profile_call({"entity": "Ada Example", "include": ["contact"]}),
        answer("Ada Example's email is ada@example.edu.", "campus_fact", ["contacts:ada"]),
        review("supported"),
    ]
    messages = [
        ChatMessage(role="user", content="Who is the convener of the Example program?"),
        ChatMessage(role="assistant", content="The published convener is Ada Example."),
        ChatMessage(role="user", content="What is that person's email?"),
    ]
    output = run_turn(messages, client=client, data=data, model="test", now=NOW)
    assert output["status"] == "answered"
    assert len(output["trace"]) == 1
    assert output["trace"][0]["resolution"]["entity"]["id"] == PERSON
    assert set(output["trace"][0]["components"]) == {"contact"}
    assert output["metrics"]["reviewCalls"] == 1
    payload = json.loads(client.create.call_args_list[-1].kwargs["input"])
    assert payload["conversation"] == [message.model_dump() for message in messages]
    assert set(payload["evidence_subjects"][key]["kind"]
               for key in payload["evidence_subjects"]) == {"contacts", "faculty"}
    assert payload["candidate"]["parts"][0]["text"] == "Ada Example's email is ada@example.edu."


@pytest.mark.parametrize("unsupported_campus_fact", [False, True])
def test_ambiguous_resolution_reaches_review_without_becoming_campus_evidence(
    unsupported_campus_fact: bool,
) -> None:
    data = person_data()
    other = copy.deepcopy(data._artifacts["campus-identities"]["entities"][0])
    other.update(id="64281a03-f2dd-4be0-8c1f-b149e2b1c21d", name="Another Example")
    other["links"] = [{"collection": "contacts", "source_key": "directory",
                       "source_record_keys": ["person:other"]}]
    data._artifacts["campus-identities"]["entities"].append(other)
    client = Mock()
    candidate = answer(
        "Professor Example is holding office hours today."
        if unsupported_campus_fact else "Which Professor Example do you mean?",
        "guidance" if unsupported_campus_fact else "clarification",
        status="answered" if unsupported_campus_fact else "clarification",
    )
    client.create.side_effect = [
        profile_call({"entity": "Professor Example", "include": ["contact"]}), candidate,
        review("unsupported_claim" if unsupported_campus_fact else "supported"),
    ]
    output = run_turn(
        [ChatMessage(role="user", content="Tell me about Professor Example.")],
        client=client, data=data, model="test", now=NOW,
    )
    data._fetch.assert_not_called()
    assert output["metrics"]["reviewCalls"] == 1
    payload = json.loads(client.create.call_args_list[-1].kwargs["input"])
    assert payload["evidence_scope"]["record_ids"] == []
    assert payload["evidence_subjects"] == {}
    resolution = payload["retrieval_coverage"][0]["resolution"]
    assert resolution["status"] == "ambiguous"
    assert resolution["total_candidates"] == 2
    assert {item["name"] for item in resolution["candidates"]} == {"Ada Example", "Another Example"}
    assert output["status"] == ("unavailable" if unsupported_campus_fact else "clarification")
    if unsupported_campus_fact:
        assert output["metrics"]["validationFailures"] == ["unsupported_claim"]
        assert "office hours" not in output["answer"]
