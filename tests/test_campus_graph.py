import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.provider import Completion, ToolCall, TurnBudget, Usage
from rockygpt_brain.retrieval import InvalidFactRequest, MemoryEntityFacts
from rockygpt_brain.retrieval.campus_graph import CampusGraph
from rockygpt_brain.turn import intake

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


def facts() -> MemoryEntityFacts:
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
