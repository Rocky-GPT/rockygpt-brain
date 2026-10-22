import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.provider import Completion, ToolCall, TurnBudget, Usage
from rockygpt_brain.retrieval import InvalidFactRequest, MemoryEntityFacts
from rockygpt_brain.retrieval.campus_graph import CampusGraph, named_by_conversation
from rockygpt_brain.turn import intake

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def facts(**contact: Any) -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="release-1",
        identity_hash="identities-1",
        entities=[
            {
                "id": "registrar",
                "name": "Registrar",
                "kind": "office",
                "aliases": [],
                "links": [
                    {
                        "collection": "contacts",
                        "source_key": "directory",
                        "source_record_keys": ["registrar"],
                    }
                ],
            }
        ],
        contacts=[
            {
                "id": "source-registrar",
                "source_key": "directory",
                "source_record_key": "registrar",
                "name": "Registrar",
                "email": "published@example.edu",
                "canonical_url": "https://example.edu/registrar",
                "collected_at": NOW,
                "freshness_sla_hours": 24,
                **contact,
            }
        ],
        now=lambda: NOW,
    )


def email_status(read: dict[str, Any]) -> str:
    status: str = next(p["status"] for p in read["properties"] if p["key"] == "email")
    return status


def test_one_lookup_ends_at_the_office_with_its_published_records() -> None:
    graph = CampusGraph(facts())
    found = graph.lookup("registrar", ["email"], NOW)
    assert [node["id"] for node in graph.path(graph.current_node)] == [
        "ramapo", "offices", "office:registrar"]
    assert found["label"] == "Registrar"
    assert email_status(found["facts"]) == "known"


def test_an_office_has_no_child_node_between_it_and_its_records() -> None:
    graph = CampusGraph(facts())
    graph.lookup("registrar", ["email"], NOW)
    assert {node["kind"] for node in graph.nodes.values()} == {"root", "category", "office"}
    assert "children" not in graph.open("office:registrar", [], NOW)


def test_fields_can_be_chosen_only_on_an_office() -> None:
    graph = CampusGraph(facts())
    graph.open("offices", [], NOW)
    with pytest.raises(InvalidFactRequest):
        graph.open("offices", ["email"], NOW)
    with pytest.raises(InvalidFactRequest):
        graph.open("ramapo", ["email"], NOW)


def test_inspecting_an_office_shows_its_records_without_choosing_fields() -> None:
    node = CampusGraph(facts()).inspect("office:registrar", NOW)
    assert [step["id"] for step in node["path"]] == ["ramapo", "offices", "office:registrar"]
    assert node["children"] == []
    assert email_status(node["facts"]) == "known"
    assert "menu" not in {p["key"] for p in node["facts"]["properties"]}


def test_the_contact_note_is_read_beside_a_requested_contact_that_is_not_published() -> None:
    note = "Testing: tests@example.edu"
    graph = CampusGraph(facts(email=None, contact_note=note))
    found = graph.lookup("registrar", ["email"], NOW)
    # The requested field and the supplementary one are kept apart.
    assert [p["key"] for p in found["facts"]["properties"]] == ["email"]
    assert email_status(found["facts"]) == "unknown"
    (supplement,) = found["also"]["properties"]
    assert supplement["key"] == "contact_note" and supplement["status"] == "known"
    assert supplement["values"][0]["value"] == note


@pytest.mark.parametrize("contact,fields,exact_name_only", [
    ({"contact_note": "Testing: tests@example.edu"}, ["email"], False),  # the email is published
    ({"email": None, "contact_note": "n"}, ["email", "contact_note"], False),  # already asked for
    ({"email": None}, ["email"], False),  # no note to read
    ({"email": None, "contact_note": "n"}, ["offices"], False),  # no contact value was asked for
    ({"email": None, "contact_note": "n"}, ["email"], True),  # an emergency route reads its own
])
def test_the_contact_note_is_not_read_when_it_would_add_nothing(
    contact: dict[str, Any], fields: list[str], exact_name_only: bool,
) -> None:
    graph = CampusGraph(facts(**contact))
    found = graph.lookup("registrar", fields, NOW, exact_name_only=exact_name_only)
    assert "also" not in found


def public_safety_candidates() -> list[dict[str, Any]]:
    shared = ["Public Safety", "Campus Police"]
    return [
        {"entity_id": "emergency", "name": "Public Safety (Emergency)",
         "aliases": [*shared, "Emergency Callboxes", "Campus Safety Escorts"], "match": "exact"},
        {"entity_id": "general", "name": "Public Safety (Non-Emergency)",
         "aliases": [*shared, "Parking Citations", "Lost & Found"], "match": "exact"},
    ]


@pytest.mark.parametrize("conversation,expected", [
    # A service only one of the tied offices lists breaks the tie.
    ("got a parking ticket\nParking citations go through Public Safety.\nwhats the number",
     "general"),
    ("where do i report a lost and found item\nwhat number", "general"),
    ("the emergency callboxes near the library\nwhat is their number", "emergency"),
    # Nothing in the conversation says which.
    ("whats the public safety number", None),
    ("campus police phone", None),
    # Both offices' services are named: still a question.
    ("lost and found or emergency callboxes, public safety number", None),
    # An office's own name never breaks a tie: "not an emergency" has the word "emergency".
    ("it is not really an emergency, what is the public safety number", None),
    ("it is a non-emergency, what is the public safety number", None),
])
def test_a_service_only_one_tied_office_lists_breaks_the_tie(
    conversation: str, expected: str | None,
) -> None:
    named = named_by_conversation(public_safety_candidates(), conversation)
    assert (named["entity_id"] if named else None) == expected


def public_safety_facts() -> MemoryEntityFacts:
    def entity(entity_id: str, name: str, aliases: list[str]) -> dict[str, Any]:
        return {"id": entity_id, "name": name, "kind": "office",
                "aliases": ["Public Safety", *aliases],
                "links": [{"collection": "contacts", "source_key": "directory",
                           "source_record_keys": [entity_id]}]}

    def record(entity_id: str, phone: str) -> dict[str, Any]:
        return {"id": f"source-{entity_id}", "source_key": "directory",
                "source_record_key": entity_id, "name": entity_id, "phone": phone,
                "canonical_url": "https://example.edu/safety", "collected_at": NOW,
                "freshness_sla_hours": 24}

    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[entity("emergency", "Public Safety (Emergency)", ["Emergency Callboxes"]),
                  entity("general", "Public Safety (Non-Emergency)", ["Parking Citations"])],
        contacts=[record("emergency", "(201) 555-0911"), record("general", "(201) 555-0432")],
        now=lambda: NOW)


def test_a_lookup_that_ties_is_broken_by_the_conversation_only_when_it_is_given() -> None:
    graph = CampusGraph(public_safety_facts())
    asked = graph.lookup("Public Safety", ["phones"], NOW)
    assert "facts" not in asked and len(asked["candidates"]) == 2
    resolved = CampusGraph(public_safety_facts()).lookup(
        "Public Safety", ["phones"], NOW, conversation="Parking citations are handled here.")
    assert resolved["label"] == "Public Safety (Non-Emergency)"
    assert resolved["by_conversation"] is True
    # An emergency route reads its own name and is never moved by the conversation.
    exact = CampusGraph(public_safety_facts()).lookup(
        "Public Safety (Emergency)", ["phones"], NOW, exact_name_only=True,
        conversation="parking citations")
    assert exact["label"] == "Public Safety (Emergency)" and "by_conversation" not in exact


def test_a_retired_records_node_is_not_in_the_graph() -> None:
    with pytest.raises(InvalidFactRequest):
        CampusGraph(facts()).inspect("records:registrar", NOW)


def call(tool: str, arguments: dict[str, Any]) -> Completion:
    output = {"type": "function_call", "call_id": "call-1", "name": tool,
              "arguments": json.dumps(arguments)}
    return Completion("", (ToolCall("call-1", tool, arguments),), [output], Usage(10, 10, 0))


class Script:
    def __init__(self, *steps: Completion) -> None:
        self.steps = iter(steps)

    async def complete(self, *, input: list[dict[str, Any]], tools: list[dict[str, Any]],
                       budget: TurnBudget) -> Completion:
        return next(self.steps)

    async def ready(self) -> bool:
        return True


def test_a_graph_lookup_turn_answers_from_the_office_and_records_its_path() -> None:
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "What is the Registrar email?"}],
         "omittedMessages": 0})
    gateway = Script(
        call("graph_lookup", {"requests": [{"query": "Registrar", "fields": ["email"]}]}),
        call("finish", {"parts": []}))
    result = asyncio.run(
        ChatEngine(gateway, facts()).answer(intake(request, now=NOW), request))
    assert result.status_code == 200
    assert result.body["status"] == "answered"
    assert "published@example.edu" in result.body["answer"]
    assert result.trace is not None
    lookup = result.trace["lookups"][0]
    assert lookup["status"] == "ok"
    assert lookup["office"] == "Registrar"
    assert [node["id"] for node in lookup["path"]] == ["ramapo", "offices", "office:registrar"]


def test_a_follow_up_about_a_parking_ticket_reaches_the_office_that_lists_it() -> None:
    def turn(messages: list[dict[str, str]]) -> dict[str, Any]:
        request = ChatRequest.model_validate({"messages": messages, "omittedMessages": 0})
        gateway = Script(
            call("graph_lookup", {"requests": [{"query": "Public Safety", "fields": ["phones"]}]}),
            call("finish", {"parts": []}))
        result = asyncio.run(
            ChatEngine(gateway, public_safety_facts()).answer(intake(request, now=NOW), request))
        assert result.trace is not None
        lookup: dict[str, Any] = result.trace["lookups"][0]
        return lookup

    ticket = turn([
        {"role": "user", "content": "got a parking ticket today and i think it was a mistake"},
        {"role": "assistant", "content": "Parking citations go through Public Safety."},
        {"role": "user", "content": "whats the number tho"}])
    assert ticket["status"] == "ok" and ticket["office"] == "Public Safety (Non-Emergency)"
    assert ticket["resolvedBy"] == "conversation"
    # With nothing in the conversation to say which, the student is still asked.
    plain = turn([{"role": "user", "content": "whats the public safety number"}])
    assert plain["status"] == "ambiguous" and "resolvedBy" not in plain
