"""Runtime trust boundaries, independent of model wording and paid providers."""

import asyncio
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from rockygpt_brain.boundary import SAFETY_MESSAGE
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import MODEL_INPUT_KEYS, ChatEngine, ChatResult
from rockygpt_brain.provider import Completion, GatewayError, ToolCall, TurnBudget, Usage
from rockygpt_brain.retrieval import EvidenceUnavailable, MemoryEntityFacts, UnknownEntity
from rockygpt_brain.turn import intake

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def completion(tool: str, arguments: dict[str, Any], *, text: str = "") -> Completion:
    return Completion(
        text,
        (ToolCall("call-1", tool, arguments),),
        [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": tool,
                "arguments": json.dumps(arguments),
            }
        ],
        Usage(10, 10, 0),
    )


def finish(kind: str | None = None, **changes: Any) -> Completion:
    return completion(
        "finish",
        {
            "parts": []
            if kind is None
            else [
                {
                    "kind": kind,
                    "message_index": None,
                    "situation": None,
                    **changes,
                }
            ]
        },
    )


LOOKUP = completion("graph_lookup", {"requests": [{"query": "Registrar", "fields": ["email"]}]})


class ScriptedGateway:
    def __init__(self, *steps: Completion | Callable[[], Completion]) -> None:
        self.steps = iter(steps)
        self.inputs: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        *,
        input: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        budget: TurnBudget,
    ) -> Completion:
        self.inputs.append(deepcopy(input))
        step = next(self.steps)
        return step() if callable(step) else step

    async def ready(self) -> bool:
        return True


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
        # Deliberately different from the turn: runtime must use the one frozen campus clock.
        now=lambda: datetime(2027, 1, 1, tzinfo=UTC),
    )


def answer(
    gateway: ScriptedGateway,
    *,
    service: MemoryEntityFacts | None = None,
    messages: list[dict[str, str]] | None = None,
    omitted: int = 0,
) -> ChatResult:
    request = ChatRequest.model_validate(
        {
            "messages": messages or [{"role": "user", "content": "What is the Registrar email?"}],
            "omittedMessages": omitted,
        }
    )
    return asyncio.run(
        ChatEngine(gateway, service or facts()).answer(
            intake(request, now=NOW),
            request,
        )
    )


@pytest.mark.parametrize(
    "bad_completion",
    [
        finish("clock", result_id="invented-id"),
        finish("facts", message_index=0),  # Conversation is not fact evidence.
        finish("clock", value="invented@example.edu"),
        completion(
            "finish",
            {"parts": [{"kind": "clock", "message_index": None, "situation": None}]},
            text="Registrar's email is invented@example.edu.",
        ),
        completion("update_student_account", {"password": "new-password"}),
        completion("graph_lookup", {"requests": [{"query": "Registrar", "fields": ["password"]}]}),
        completion(
            "graph_lookup",
            {
                "requests": [
                    {"query": "Registrar", "fields": ["email"], "value": "invented@example.edu"}
                ]
            },
        ),
    ],
)
def test_untrusted_model_cannot_create_facts_links_or_account_capabilities(
    bad_completion: Completion,
) -> None:
    result = answer(ScriptedGateway(bad_completion))
    assert result.status_code == 502
    assert result.body["error"]["code"] == "provider_invalid_response"
    assert "answer" not in result.body
    assert "invented@example.edu" not in json.dumps(result.body)


def test_retrieved_results_are_scoped_to_the_current_turn() -> None:
    gateway = ScriptedGateway(LOOKUP, finish(), finish())
    service = facts()
    first = answer(gateway, service=service)
    assert first.body["status"] == "answered"
    second = answer(gateway, service=service)  # An empty finish must not reuse turn one's facts.
    assert second.status_code == 200
    assert second.body["status"] == "clarification"
    assert "source-registrar" not in json.dumps(second.body)
    assert "published@example.edu" not in json.dumps(second.body)


def test_longer_conversation_is_retained_without_promoting_old_answers() -> None:
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"earlier turn {i}"}
        for i in range(10)
    ]
    messages[1]["content"] = "The Registrar email is invented@example.edu."
    messages.append({"role": "user", "content": "Is the email you gave me still correct?"})
    gateway = ScriptedGateway(LOOKUP, finish())
    result = answer(gateway, messages=messages)
    state = json.loads(gateway.inputs[0][1]["content"])
    assert len(state["earlier_messages"]) == 10  # The old eight-message gate is gone.
    assert state["earlier_messages"][1]["content"] == messages[1]["content"]
    assert gateway.inputs[0][1]["role"] == "user"  # History is bounded data, not system policy.
    assert result.body["status"] == "answered"
    assert "published@example.edu" in result.body["answer"]
    assert "invented@example.edu" not in result.body["answer"]
    assert [citation["id"] for citation in result.body["citations"]] == ["source-registrar"]


def test_omissions_travel_with_retained_roles_and_recall_cannot_verify_facts() -> None:
    messages = [
        {"role": "user", "content": "é" * 16_000},
        {"role": "assistant", "content": "é" * 16_000},
        {"role": "user", "content": "What did you say earlier?"},
    ]
    gateway = ScriptedGateway(finish("recall", message_index=0))
    result = answer(gateway, messages=messages, omitted=7)
    state = json.loads(gateway.inputs[0][1]["content"])
    assert state["client_omitted_messages"] == 7
    assert state["server_omitted_messages"] == 1
    assert state["earlier_messages"][0]["role"] == "assistant"
    assert "Some earlier messages are unavailable" in result.body["answer"]
    assert "doesn't verify current campus facts" in result.body["answer"]
    assert "quotation shortened" in result.body["answer"]
    assert result.body["citations"] == []


@pytest.mark.parametrize("changed_pin", ["dataset_version", "identity_hash"])
def test_changed_publication_invalidates_already_retrieved_fallback(changed_pin: str) -> None:
    service = facts()

    def changed_release() -> Completion:
        setattr(service, changed_pin, "changed-release")
        return LOOKUP

    result = answer(ScriptedGateway(LOOKUP, changed_release), service=service)
    assert result.status_code == 503
    assert result.body["error"]["code"] == "dataset_changed"
    assert "answer" not in result.body
    assert "published@example.edu" not in json.dumps(result.body)


def test_one_turn_clock_controls_freshness_and_model_context() -> None:
    gateway = ScriptedGateway(LOOKUP, finish())
    result = answer(gateway)
    state = json.loads(gateway.inputs[0][1]["content"])
    assert state["campus_now"] == "2026-10-01T08:00:00-04:00"
    assert result.body["status"] == "answered"
    assert result.body["citations"][0]["freshness"] == "fresh"
    assert "stale" not in result.body["answer"]


def test_account_limitation_cannot_discard_public_part_already_retrieved() -> None:
    result = answer(
        ScriptedGateway(LOOKUP, finish("account_limit")),
        messages=[{"role": "user", "content": "Change my major and give me the Registrar email."}],
    )
    assert result.status_code == 200
    assert result.body["status"] == "partial"
    assert "can't see or change your personal student information" in result.body["answer"]
    assert "published@example.edu" in result.body["answer"]
    assert [citation["id"] for citation in result.body["citations"]] == ["source-registrar"]


def test_model_cannot_omit_missing_evidence_to_report_full_success() -> None:
    lookup = completion(
        "graph_lookup",
        {
            "requests": [
                {"query": "Registrar", "fields": ["email"]},
                {"query": "Registrar", "fields": ["offices"]},
            ]
        },
    )
    result = answer(
        ScriptedGateway(lookup, finish()),
        messages=[{"role": "user", "content": "Give me the Registrar email and office location."}],
    )
    assert result.status_code == 200
    assert result.body["status"] == "partial"
    assert "published@example.edu" in result.body["answer"]
    assert "Offices: not published in the available evidence" in result.body["answer"]


@pytest.mark.parametrize(("kind", "phrase"), [
    ("greeting", "Which office do you need?"),
    ("thanks", "You're welcome"),
    ("okay", "call 911, or call or text 988"),
    ("about", "I can't see your personal student records"),
])
def test_small_talk_gets_a_fixed_server_written_reply_not_an_error(kind: str, phrase: str) -> None:
    result = answer(ScriptedGateway(finish(kind)),
                    messages=[{"role": "user", "content": "hey"}])
    assert result.status_code == 200
    assert result.body["status"] == "answered"
    assert phrase in result.body["answer"]
    assert result.body["citations"] == []


def test_a_finish_with_nothing_in_it_asks_what_the_student_needs_instead_of_failing() -> None:
    result = answer(ScriptedGateway(finish()), messages=[{"role": "user", "content": "thx"}])
    assert result.status_code == 200
    assert result.body["status"] == "clarification"
    assert result.body["answer"] == "Which office or service, and which details, do you mean?"


def test_safety_still_shows_the_contact_the_student_asked_for() -> None:
    result = answer(ScriptedGateway(LOOKUP, finish("safety")),
                    messages=[{"role": "user", "content": "She's awake now. Registrar email?"}])
    text = result.body["answer"]
    assert result.status_code == 200 and result.body["status"] == "partial"
    assert text.startswith("If you or someone else is in danger right now, call 911.")
    assert "published@example.edu" in text
    assert [c["id"] for c in result.body["citations"]] == ["source-registrar"]


def test_safety_alone_is_just_the_safety_text() -> None:
    result = answer(ScriptedGateway(finish("safety")),
                    messages=[{"role": "user", "content": "my roommate collapsed"}])
    assert result.body["answer"].startswith("If you or someone else is in danger right now")
    assert "published@example.edu" not in result.body["answer"]
    assert result.body["citations"] == []


def two_student_offices() -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": f"office-{n}", "name": name, "kind": "office", "aliases": [], "links": []}
                  for n, name in enumerate(("Student Accounts", "Student Conduct"))],
        contacts=[], now=lambda: NOW)


def student_lookup(query: str) -> Completion:
    return completion("graph_lookup", {"requests": [{"query": query, "fields": ["email"]}]})


def test_safety_keeps_the_question_about_which_office_was_meant() -> None:
    result = answer(ScriptedGateway(student_lookup("student"), finish("safety")),
                    service=two_student_offices())
    text = result.body["answer"]
    assert text.startswith(SAFETY_MESSAGE)
    assert "Which office do you mean: Student Accounts, Student Conduct?" in text
    assert result.body["status"] == "partial"


def test_safety_keeps_the_note_that_no_office_matched() -> None:
    result = answer(ScriptedGateway(student_lookup("Cafeteria"), finish("safety")))
    assert result.body["answer"].startswith(SAFETY_MESSAGE)
    assert "couldn't find a matching office" in result.body["answer"]


def test_safety_does_not_hide_a_data_outage() -> None:
    class Down(MemoryEntityFacts):
        def search_offices(self, *_: Any, **__: Any) -> dict[str, Any]:
            raise EvidenceUnavailable("down")

    service = facts()
    down = Down(dataset_version="release-1", identity_hash="identities-1",
                entities=service.entities, contacts=service.contacts, now=lambda: NOW)
    result = answer(ScriptedGateway(LOOKUP, finish("safety")), service=down)
    assert result.status_code == 503 and result.body["error"]["code"] == "data_unavailable"
    assert result.body["error"]["emergency"]["text"] == SAFETY_MESSAGE


def test_safety_parts_are_validated_like_every_other_part() -> None:
    result = answer(ScriptedGateway(finish("safety", message_index=0)))
    assert result.status_code == 502 and result.body["error"]["code"] == "provider_invalid_response"


def test_an_empty_finish_right_after_a_safety_reply_repeats_the_safety_text() -> None:
    result = answer(ScriptedGateway(finish()), messages=[
        {"role": "user", "content": "my roommate collapsed"},
        {"role": "assistant", "content": SAFETY_MESSAGE},
        {"role": "user", "content": "please hurry"},
    ])
    assert result.status_code == 200 and result.body["answer"] == SAFETY_MESSAGE
    ordinary = answer(ScriptedGateway(finish()), messages=[
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hi! Which office do you need?"},
        {"role": "user", "content": "thx"},
    ])
    assert ordinary.body["status"] == "clarification"


def test_a_reply_cut_short_by_a_provider_failure_still_carries_the_emergency_numbers() -> None:
    def provider_down() -> Completion:
        raise GatewayError("provider_unavailable")

    result = answer(ScriptedGateway(LOOKUP, provider_down))
    assert result.body["status"] == "partial"
    assert "published@example.edu" in result.body["answer"]
    assert result.body["answer"].endswith(SAFETY_MESSAGE)


def test_a_lookup_that_already_asks_which_office_is_not_asked_twice() -> None:
    both = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": f"office-{n}", "name": name, "kind": "office", "aliases": [], "links": []}
                  for n, name in enumerate(("Student Accounts", "Student Conduct"))],
        contacts=[], now=lambda: NOW)
    lookup = completion("graph_lookup", {"requests": [{"query": "student", "fields": ["email"]}]})
    result = answer(ScriptedGateway(lookup, finish("clarification")), service=both)
    text = result.body["answer"]
    assert "Which office do you mean: Student Accounts, Student Conduct?" in text
    assert "Which office or service, and which details, do you mean?" not in text
    assert result.body["status"] == "clarification"


def test_the_model_is_given_only_the_ramapo_root_not_the_office_list() -> None:
    service = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[
            {"id": "b", "name": "ID Card Room", "kind": "office", "aliases": ["Husky Card"],
             "links": []},
            {"id": "a", "name": "Registrar", "kind": "office", "aliases": [], "links": []},
            {"id": "p", "name": "A Person", "kind": "person", "aliases": [], "links": []},
        ],
        contacts=[], now=lambda: NOW)
    gateway = ScriptedGateway(finish("greeting"))
    answer(gateway, service=service)
    state = json.loads(gateway.inputs[0][1]["content"])
    assert tuple(state) == MODEL_INPUT_KEYS
    root = state["graph_root"]
    assert root["id"] == "ramapo" and [child["id"] for child in root["children"]] == ["offices"]
    # No office name or alias reaches the model until a lookup walks the graph for it.
    assert "ID Card Room" not in json.dumps(state) and "Husky Card" not in json.dumps(state)


def test_office_listing_is_sorted_deduplicated_bounded_and_pinned() -> None:
    service = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[
            {"id": "c", "name": "Zeta Office", "kind": "office", "aliases": [], "links": []},
            {"id": "a", "name": "Alpha Office", "kind": "office",
             "aliases": ["Beta", "Alpha", "Beta"], "links": []},
            {"id": "b", "name": "Middle Office", "kind": "office", "aliases": [], "links": []},
        ],
        contacts=[], now=lambda: NOW)
    listing = service.list_offices()
    assert listing["offices"] == [
        {"name": "Alpha Office", "aliases": ["Alpha", "Beta"]},
        {"name": "Middle Office", "aliases": []},
        {"name": "Zeta Office", "aliases": []},
    ]
    assert listing["dataset_version"] == "release-1" and listing["truncated"] is False
    shorter = service.list_offices(limit=2)
    assert [o["name"] for o in shorter["offices"]] == ["Alpha Office", "Middle Office"]
    assert shorter["truncated"] is True
    with pytest.raises(Exception, match="between 1 and 500"):
        service.list_offices(limit=0)
    with pytest.raises(Exception, match="changed"):
        service.list_offices(dataset_version="other")


@pytest.mark.parametrize("changed_pin", ["dataset_version", "identity_hash"])
def test_a_second_lookup_must_match_the_publication_the_first_one_read(changed_pin: str) -> None:
    service = facts()

    def changed_release() -> Completion:
        setattr(service, changed_pin, "changed-release")
        return LOOKUP

    result = answer(ScriptedGateway(LOOKUP, changed_release, finish()), service=service)
    assert result.status_code == 503 and result.body["error"]["code"] == "dataset_changed"
    assert "published@example.edu" not in json.dumps(result.body)  # No half-old, half-new answer.


@pytest.mark.parametrize("changed_pin", ["dataset_version", "identity_hash"])
def test_the_first_lookup_defines_the_publication_a_turn_answers_from(changed_pin: str) -> None:
    service = facts()

    def changed_before_any_lookup() -> Completion:
        setattr(service, changed_pin, "changed-release")
        return LOOKUP

    result = answer(ScriptedGateway(changed_before_any_lookup, finish()), service=service)
    assert result.status_code == 200 and "published@example.edu" in result.body["answer"]
    if changed_pin == "dataset_version":
        assert result.body["datasetVersion"] == "changed-release"


def test_a_data_outage_fails_a_lookup_but_not_a_turn_that_needs_no_data() -> None:
    class Broken(MemoryEntityFacts):
        def list_offices(self, **_: Any) -> dict[str, Any]:
            raise EvidenceUnavailable("down")

    service = facts()
    broken = Broken(dataset_version="release-1", identity_hash="identities-1",
                    entities=service.entities, contacts=service.contacts, now=lambda: NOW)
    result = answer(ScriptedGateway(LOOKUP, finish()), service=broken)
    assert result.status_code == 503 and result.body["error"]["code"] == "data_unavailable"
    assert result.body["error"]["retryable"] is True
    # The directory is first read by a lookup, so a greeting does not need it.
    greeting = answer(ScriptedGateway(finish("greeting")), service=broken)
    assert greeting.status_code == 200 and greeting.body["status"] == "answered"


def test_the_trace_records_each_lookup_the_model_asked_for_and_how_it_ended() -> None:
    result = answer(ScriptedGateway(LOOKUP, finish("unsupported")))
    assert result.trace is not None and "trace" not in result.body  # The API decides who sees it.
    assert result.trace["decidedBy"] == "model" and result.trace["root"]["id"] == "ramapo"
    assert result.trace["finish"] == ["unsupported"]
    (lookup,) = result.trace["lookups"]
    assert (lookup["tool"], lookup["traversedBy"]) == ("graph_lookup", "code")
    assert lookup["arguments"] == {"query": "Registrar", "fields": ["email"]}
    assert (lookup["status"], lookup["result_count"], lookup["office"]) == ("ok", 1, "Registrar")
    assert [node["id"] for node in lookup["path"]] == ["ramapo", "offices", "office:registrar"]
    assert (lookup["dataset_version"], lookup["identity_hash"]) == ("release-1", "identities-1")


def test_the_trace_says_when_a_lookup_was_ambiguous_missing_or_down() -> None:
    ambiguous = answer(ScriptedGateway(student_lookup("student"), finish()),
                       service=two_student_offices())
    assert ambiguous.trace is not None
    assert ambiguous.trace["lookups"][0]["status"] == "ambiguous"
    assert ambiguous.trace["lookups"][0]["candidates"] == ["Student Accounts", "Student Conduct"]
    missing = answer(ScriptedGateway(student_lookup("Cafeteria"), finish()))
    assert missing.trace is not None
    assert missing.trace["lookups"][0]["status"] == "not_found"
    assert missing.trace["lookups"][0]["result_count"] == 0

    class Down(MemoryEntityFacts):
        def search_offices(self, *_: Any, **__: Any) -> dict[str, Any]:
            raise EvidenceUnavailable("down")

    service = facts()
    down = Down(dataset_version="release-1", identity_hash="identities-1",
                entities=service.entities, contacts=service.contacts, now=lambda: NOW)
    outage = answer(ScriptedGateway(LOOKUP, finish()), service=down)
    assert outage.trace is not None and outage.trace["lookups"][0]["status"] == "data_unavailable"


def test_a_turn_with_no_lookup_still_says_what_the_model_chose() -> None:
    result = answer(ScriptedGateway(finish("greeting")))
    assert result.trace is not None
    assert result.trace["lookups"] == [] and result.trace["finish"] == ["greeting"]


class MeteredGateway(ScriptedGateway):
    """Spends like the real gateway: every call adds to the turn's budget."""

    async def complete(self, *, input: list[dict[str, Any]], tools: list[dict[str, Any]],
                       budget: TurnBudget) -> Completion:
        budget.calls += 1
        budget.committed_nusd += 7
        return await super().complete(input=input, tools=tools, budget=budget)


def test_the_trace_counts_model_calls_and_spend_even_when_the_turn_fails() -> None:
    done = answer(MeteredGateway(LOOKUP, finish()))
    assert done.trace is not None
    assert (done.trace["modelCalls"], done.trace["committedNusd"]) == (2, 14)

    def provider_down() -> Completion:
        raise GatewayError("provider_unavailable")

    broken = answer(MeteredGateway(LOOKUP, provider_down))
    assert broken.trace is not None
    assert (broken.trace["modelCalls"], broken.trace["committedNusd"]) == (2, 14)


def test_a_turn_ended_by_an_error_says_so_and_keeps_the_lookups_it_made() -> None:
    def provider_down() -> Completion:
        raise GatewayError("provider_unavailable")

    cut_short = answer(ScriptedGateway(LOOKUP, provider_down))
    assert cut_short.status_code == 200 and cut_short.trace is not None
    assert cut_short.trace["decidedBy"] == "error"
    assert cut_short.trace["errorCode"] == "provider_unavailable"
    assert [c["status"] for c in cut_short.trace["lookups"]] == ["ok"]

    no_lookup = answer(ScriptedGateway(provider_down))
    assert no_lookup.status_code == 503 and no_lookup.trace is not None
    assert (no_lookup.trace["decidedBy"], no_lookup.trace["lookups"]) == ("error", [])

    class Broken(MemoryEntityFacts):
        def list_offices(self, **_: Any) -> dict[str, Any]:
            raise EvidenceUnavailable("down")

    service = facts()
    broken = Broken(dataset_version="release-1", identity_hash="identities-1",
                    entities=service.entities, contacts=service.contacts, now=lambda: NOW)
    outage = answer(ScriptedGateway(LOOKUP, finish()), service=broken)
    assert outage.trace is not None and outage.trace["errorCode"] == "data_unavailable"
    assert [c["status"] for c in outage.trace["lookups"]] == ["data_unavailable"]


@pytest.mark.parametrize("changed_pin", ["dataset_version", "identity_hash"])
def test_a_lookup_that_hits_a_changed_publication_still_shows_in_the_trace(
        changed_pin: str) -> None:
    service = facts()

    def changed_release() -> Completion:
        setattr(service, changed_pin, "changed-release")
        return LOOKUP

    result = answer(ScriptedGateway(LOOKUP, changed_release, finish()), service=service)
    assert result.trace is not None and result.trace["errorCode"] == "dataset_changed"
    assert [c["status"] for c in result.trace["lookups"]] == ["ok", "dataset_changed"]


def test_a_lookup_the_data_rejects_shows_in_the_trace() -> None:
    class Gone(MemoryEntityFacts):
        def get_office_facts(self, *_: Any, **__: Any) -> dict[str, Any]:
            raise UnknownEntity("gone")

    service = facts()
    gone = Gone(dataset_version="release-1", identity_hash="identities-1",
                entities=service.entities, contacts=service.contacts, now=lambda: NOW)
    result = answer(ScriptedGateway(LOOKUP, finish()), service=gone)
    assert result.trace is not None
    assert [c["status"] for c in result.trace["lookups"]] == ["rejected"]


def test_the_trace_shows_five_candidates_and_how_many_matched() -> None:
    many = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": f"o{n}", "name": f"Student Office {n}", "kind": "office", "aliases": [],
                   "links": []} for n in range(7)],
        contacts=[], now=lambda: NOW)
    result = answer(ScriptedGateway(student_lookup("student"), finish()), service=many)
    assert result.trace is not None
    entry = result.trace["lookups"][0]
    assert entry["status"] == "ambiguous" and entry["result_count"] == 7
    assert entry["candidates"] == [f"Student Office {n}" for n in range(5)]
    assert entry["truncated"] is False


def test_an_all_clear_is_not_answered_as_a_thank_you() -> None:
    result = answer(ScriptedGateway(finish("okay")), messages=[
        {"role": "user", "content": "someone collapsed in the caf"},
        {"role": "assistant", "content": SAFETY_MESSAGE},
        {"role": "user", "content": "update: it was a false alarm, no need to send anyone"},
    ])
    assert result.status_code == 200 and result.body["status"] == "answered"
    assert "You're welcome" not in result.body["answer"]
    assert "911" in result.body["answer"] and result.body["citations"] == []


def test_a_named_office_is_looked_up_even_when_the_rest_cannot_be_answered() -> None:
    result = answer(ScriptedGateway(LOOKUP, finish("unsupported")), messages=[
        {"role": "user", "content": "is the registrar open fridays"}])
    assert "published@example.edu" in result.body["answer"]
    assert "I don't have verified information" in result.body["answer"]
    assert result.body["status"] == "partial"


def test_a_name_the_student_asked_for_keeps_its_conflicts_and_unknowns() -> None:
    asked = completion("graph_lookup", {"requests": [
        {"query": "Registrar", "fields": ["name", "email"]}]})
    text = answer(ScriptedGateway(asked, finish())).body["answer"]
    assert "published@example.edu" in text and "Name" in text


def test_the_clock_and_the_which_office_line_use_the_shared_texts() -> None:
    from zoneinfo import ZoneInfo

    from rockygpt_brain.engine import AMBIGUOUS_MORE_TEXT, CLOCK_FORMAT, CLOCK_TEXT

    clock = answer(ScriptedGateway(finish("clock")))
    campus_now = NOW.astimezone(ZoneInfo("America/New_York"))
    assert clock.body["answer"] == CLOCK_TEXT.format(when=campus_now.strftime(CLOCK_FORMAT))
    many = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": f"o{n}", "name": f"Student Office {n}", "kind": "office", "aliases": [],
                   "links": []} for n in range(12)],
        contacts=[], now=lambda: NOW)
    result = answer(ScriptedGateway(student_lookup("student office"), finish()), service=many)
    assert result.trace is not None and result.trace["lookups"][0]["truncated"] is True
    assert result.body["answer"].endswith(AMBIGUOUS_MORE_TEXT.strip())


def campus_help_facts() -> MemoryEntityFacts:
    """The registrar, plus the two offices an emergency reply points to, each with a phone."""
    names = (("registrar", "Registrar", "(201) 555-0100"),
             ("public-safety", "Public Safety (Emergency)", "(201) 555-6666"),
             ("counseling", "Counseling Center", "(201) 555-7522"))
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": key, "name": name, "kind": "office", "aliases": [],
                   "links": [{"collection": "contacts", "source_key": "directory",
                              "source_record_keys": [key]}]} for key, name, _ in names],
        contacts=[{"id": f"source-{key}", "source_key": "directory", "source_record_key": key,
                   "name": name, "email": f"{key}@example.edu", "phone": phone,
                   "canonical_url": f"https://example.edu/{key}", "collected_at": NOW,
                   "freshness_sla_hours": 24} for key, name, phone in names],
        now=lambda: NOW)


def test_the_model_names_the_kind_of_emergency_and_gets_that_text_first() -> None:
    from rockygpt_brain.boundary import SAFETY_TEXTS

    for kind in ("self_harm", "medical", "danger", "fire", "other"):
        result = answer(ScriptedGateway(finish("safety", situation=kind)),
                        messages=[{"role": "user", "content": "help"}])
        assert result.body["answer"] == SAFETY_TEXTS[kind]
        assert result.trace is not None and result.trace["situation"] == kind
    unnamed = answer(ScriptedGateway(finish("safety")), messages=[{"role": "user", "content": "h"}])
    assert unnamed.body["answer"] == SAFETY_MESSAGE


def test_two_different_kinds_of_emergency_get_the_general_text() -> None:
    both = completion("finish", {"parts": [
        {"kind": "safety", "message_index": None, "situation": "fire"},
        {"kind": "safety", "message_index": None, "situation": "medical"}]})
    assert answer(ScriptedGateway(both)).body["answer"] == SAFETY_MESSAGE
    same = completion("finish", {"parts": [
        {"kind": "safety", "message_index": None, "situation": "fire"},
        {"kind": "safety", "message_index": None, "situation": "fire"}]})
    from rockygpt_brain.boundary import SAFETY_TEXTS
    assert answer(ScriptedGateway(same)).body["answer"] == SAFETY_TEXTS["fire"]


def test_a_situation_on_a_part_that_is_not_safety_is_ignored() -> None:
    result = answer(ScriptedGateway(finish("greeting", situation="fire")),
                    messages=[{"role": "user", "content": "hi"}])
    assert result.body["answer"].startswith("Hi! I'm RockyGPT")
    assert "911" not in result.body["answer"] and result.body["status"] == "answered"


def test_an_emergency_reply_adds_the_published_campus_numbers_with_sources() -> None:
    from rockygpt_brain.boundary import SAFETY_TEXTS
    from rockygpt_brain.engine import HELP_TEXT

    result = answer(ScriptedGateway(finish("safety", situation="fire")),
                    service=campus_help_facts(),
                    messages=[{"role": "user", "content": "there is smoke"}])
    text = result.body["answer"]
    assert text.startswith(SAFETY_TEXTS["fire"] + "\n\n" + HELP_TEXT)
    assert "Public Safety \\(Emergency\\)" in text and "+12015556666" in text
    assert "Counseling" not in text and "+12015557522" not in text
    assert [c["id"] for c in result.body["citations"]] == ["source-public-safety"]
    assert result.body["status"] == "partial"
    assert result.trace is not None
    entry = result.trace["lookups"][0]
    assert entry["tool"] == "emergency_contacts" and entry["status"] == "ok"
    assert entry["office"] == "Public Safety (Emergency)"
    assert entry["arguments"] == {"query": "Public Safety (Emergency)", "fields": ["phones"]}


def test_a_self_harm_reply_also_points_to_the_counseling_center() -> None:
    result = answer(ScriptedGateway(finish("safety", situation="self_harm")),
                    service=campus_help_facts(),
                    messages=[{"role": "user", "content": "i want to disappear"}])
    text = result.body["answer"]
    assert text.index("988") < text.index("+12015557522") < text.index("+12015556666")
    assert {c["id"] for c in result.body["citations"]} == {"source-counseling",
                                                          "source-public-safety"}


def test_the_rest_of_the_request_follows_the_emergency_text_and_numbers() -> None:
    lookup = completion("graph_lookup", {"requests": [{"query": "Registrar", "fields": ["email"]}]})
    result = answer(ScriptedGateway(lookup, finish("safety", situation="medical")),
                    service=campus_help_facts(),
                    messages=[{"role": "user", "content": "she fainted, also registrar email"}])
    text = result.body["answer"]
    assert text.index("Call 911") < text.index("+12015556666") < text.index("registrar@example.edu")


def test_an_office_the_model_already_looked_up_is_not_looked_up_twice() -> None:
    lookup = completion("graph_lookup", {"requests": [
        {"query": "Public Safety (Emergency)", "fields": ["email", "phones"]}]})
    result = answer(ScriptedGateway(lookup, finish("safety", situation="danger")),
                    service=campus_help_facts(),
                    messages=[{"role": "user", "content": "someone is following me"}])
    assert result.body["answer"].count("+12015556666") == 1
    assert result.trace is not None
    assert [e["tool"] for e in result.trace["lookups"]] == ["graph_lookup"]


def test_emergency_text_still_comes_when_the_campus_numbers_cannot_be_read() -> None:
    from rockygpt_brain.boundary import SAFETY_TEXTS

    class Down(MemoryEntityFacts):
        def search_offices(self, *_: Any, **__: Any) -> dict[str, Any]:
            raise EvidenceUnavailable("down")

    base = campus_help_facts()
    down = Down(dataset_version="release-1", identity_hash="identities-1",
                entities=base.entities, contacts=base.contacts, now=lambda: NOW)
    result = answer(ScriptedGateway(finish("safety", situation="fire")), service=down)
    assert result.status_code == 200 and result.body["answer"] == SAFETY_TEXTS["fire"]
    assert result.body["citations"] == []
    assert result.trace is not None and result.trace["lookups"][0]["status"] == "failed"


def test_a_slow_read_of_the_campus_numbers_never_delays_the_emergency_text(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from rockygpt_brain import engine as engine_module
    from rockygpt_brain.boundary import SAFETY_TEXTS

    class Slow(MemoryEntityFacts):
        def search_offices(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            time.sleep(0.4)
            return super().search_offices(*args, **kwargs)

    base = campus_help_facts()
    slow = Slow(dataset_version="release-1", identity_hash="identities-1",
                entities=base.entities, contacts=base.contacts, now=lambda: NOW)
    monkeypatch.setattr(engine_module, "HELP_SECONDS", 0.05)
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": "help"}]})
    timing: list[float] = []

    async def timed() -> ChatResult:
        started = time.monotonic()
        # The reply returns at the timeout. The slow read is left to finish on its own thread.
        found = await ChatEngine(ScriptedGateway(finish("safety", situation="medical")),
                                 slow).answer(intake(request, now=NOW), request)
        timing.append(time.monotonic() - started)
        return found

    result = asyncio.run(timed())
    assert timing[0] < 0.3
    assert result.body["answer"] == SAFETY_TEXTS["medical"]
    assert result.trace is not None and result.trace["lookups"][0]["status"] == "timeout"


def test_the_phrase_floor_reply_has_its_own_text_and_the_campus_numbers() -> None:
    from rockygpt_brain.boundary import SAFETY_TEXTS, check
    from rockygpt_brain.engine import HELP_TEXT

    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "there is a fire in my dorm"}]})
    turn = intake(request, now=NOW)
    boundary = check(turn)
    engine_ = ChatEngine(ScriptedGateway(), campus_help_facts())
    result = asyncio.run(engine_.safety_reply(turn, boundary.situation))
    assert boundary.situation == "fire"
    assert result.body["answer"].startswith(SAFETY_TEXTS["fire"] + "\n\n" + HELP_TEXT)
    assert "+12015556666" in result.body["answer"]
    assert result.trace is not None
    assert (result.trace["decidedBy"], result.trace["modelCalls"],
            result.trace["situation"]) == ("phrase_floor", 0, "fire")


def test_an_empty_finish_after_any_emergency_text_repeats_that_text() -> None:
    from rockygpt_brain.boundary import SAFETY_TEXTS

    result = answer(ScriptedGateway(finish()), messages=[
        {"role": "user", "content": "there is a fire"},
        {"role": "assistant", "content": SAFETY_TEXTS["fire"] + "\n\nOn campus: ..."},
        {"role": "user", "content": "ok"},
    ])
    assert result.body["answer"] == SAFETY_TEXTS["fire"]


def test_a_refusal_points_at_the_office_shown_above_it_and_otherwise_asks_for_one() -> None:
    from rockygpt_brain.boundary import CAPABILITY_AFTER_LOOKUP_MESSAGE, CAPABILITY_MESSAGE
    from rockygpt_brain.engine import UNSUPPORTED_AFTER_LOOKUP_MESSAGE, UNSUPPORTED_MESSAGE

    messages = [{"role": "user", "content": "is the registrar open fridays"}]
    with_office = answer(ScriptedGateway(LOOKUP, finish("unsupported")), messages=messages)
    assert with_office.body["answer"].endswith(UNSUPPORTED_AFTER_LOOKUP_MESSAGE)
    alone = answer(ScriptedGateway(finish("unsupported")), messages=messages)
    assert alone.body["answer"] == UNSUPPORTED_MESSAGE
    assert "tell me which office" in UNSUPPORTED_MESSAGE
    own = answer(ScriptedGateway(LOOKUP, finish("account_limit")), messages=messages)
    assert own.body["answer"].endswith(CAPABILITY_AFTER_LOOKUP_MESSAGE)
    assert answer(ScriptedGateway(finish("account_limit")),
                  messages=messages).body["answer"] == CAPABILITY_MESSAGE
    # An office lookup that found nothing shows no details, so nothing is "above".
    nothing = answer(ScriptedGateway(student_lookup("Cafeteria"), finish("unsupported")))
    assert UNSUPPORTED_AFTER_LOOKUP_MESSAGE not in nothing.body["answer"]


def test_a_recalled_reply_is_quoted_as_plain_words() -> None:
    earlier = ("**Registrar**\n\nEmail: registrar@example.edu "
               "[directory](https://example.edu/directory)\n\n"
               "**Public Safety \\(Emergency\\)**")
    result = answer(ScriptedGateway(finish("recall", message_index=1)), messages=[
        {"role": "user", "content": "registrar email"},
        {"role": "assistant", "content": earlier},
        {"role": "user", "content": "what did you say first"},
    ])
    quote = result.body["answer"]
    assert "**" not in quote and "](" not in quote and "https://" not in quote
    assert "> Registrar" in quote and "Email: registrar@example.edu directory" in quote
    assert "Public Safety \\(Emergency\\)" in quote  # Escaped once, as plain text.
    own = answer(ScriptedGateway(finish("recall", message_index=0)), messages=[
        {"role": "user", "content": "I typed **this** and [that](x)"},
        {"role": "user", "content": "what did i say"},
    ])
    assert "I typed \\*\\*this\\*\\*" in own.body["answer"]  # A student's own words are kept.


def hours_facts() -> MemoryEntityFacts:
    base = facts()
    days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    entity = deepcopy(base.entities[0])
    entity["links"].append({"collection": "campus_hours", "source_key": "campus-hours",
                            "source_record_keys": [f"Registrar:{day}" for day in days]})
    schedules = [{
        "id": f"hours-{day}", "source_key": "campus-hours", "source_record_key": f"Registrar:{day}",
        "name": "Registrar", "day": day,
        "schedule": "8:30am-4:30pm" if day not in ("Saturday", "Sunday") else "Hours unavailable",
        "notes": "Fall/Spring Hours: 8:30 A.M. - 4:30 P.M. Monday - Friday",
        "source_url": "https://example.edu/registrar", "collected_at": NOW,
        "valid_from": "2026-08-26", "valid_until": "2026-12-16", "content_hash": f"h-{day}",
        "canonical_url": "https://example.edu/campus-hours", "freshness_sla_hours": 4_320,
    } for day in days]
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1", entities=[entity],
        contacts=base.contacts, schedules=schedules, now=lambda: NOW)


def test_the_model_can_ask_for_hours_and_the_code_writes_them_with_their_source() -> None:
    asked = completion("graph_lookup", {"requests": [
        {"query": "Registrar", "fields": ["email", "phones", "offices", "hours"]}]})
    result = answer(ScriptedGateway(asked, finish()), service=hours_facts(),
                    messages=[{"role": "user", "content": "is the registrar open fridays"}])
    text = result.body["answer"]
    assert "Monday to Friday: 8:30am-4:30pm" in text and "Saturday and Sunday" in text
    assert "published validity 2026-08-26 through 2026-12-16" in text
    assert {c["collection"] for c in result.body["citations"]} == {"contacts", "campus_hours"}
    # The fixture publishes no phone or room, so the reply is partial for that reason only.
    assert "Phones: not published" in text and result.body["status"] == "partial"


def test_hours_for_an_office_without_a_schedule_say_not_published() -> None:
    asked = completion("graph_lookup", {"requests": [
        {"query": "Registrar", "fields": ["email", "hours"]}]})
    result = answer(ScriptedGateway(asked, finish("unsupported")),
                    messages=[{"role": "user", "content": "registrar hours"}])
    assert "Hours: not published in the available evidence." in result.body["answer"]
    assert "published@example.edu" in result.body["answer"]


def test_a_model_emergency_near_the_turn_deadline_still_gets_its_own_text(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from rockygpt_brain import engine as engine_module
    from rockygpt_brain.boundary import SAFETY_TEXTS

    class SlowFacts(MemoryEntityFacts):
        def search_offices(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            time.sleep(0.6)
            return super().search_offices(*args, **kwargs)

    class SlowGateway(ScriptedGateway):
        async def complete(self, **kwargs: Any) -> Completion:
            await asyncio.sleep(0.4)  # The model answers 0.1 s before the turn's 0.5 s deadline.
            return await super().complete(**kwargs)

    base = campus_help_facts()
    slow = SlowFacts(dataset_version="release-1", identity_hash="identities-1",
                     entities=base.entities, contacts=base.contacts, now=lambda: NOW)
    monkeypatch.setattr(engine_module, "HELP_SECONDS", 0.2)
    request = ChatRequest.model_validate({"messages": [{"role": "user", "content": "smoke"}]})
    result = asyncio.run(ChatEngine(
        SlowGateway(finish("safety", situation="fire")), slow, turn_seconds=0.5,
    ).answer(intake(request, now=NOW), request))
    assert result.status_code == 200 and result.body["answer"] == SAFETY_TEXTS["fire"]
    assert result.trace is not None and result.trace["lookups"][0]["status"] == "timeout"


def test_only_the_exact_published_office_name_is_used_for_emergency_numbers() -> None:
    from rockygpt_brain.boundary import SAFETY_TEXTS

    near = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "ns", "name": "Public Safety (Non-Emergency)", "kind": "office",
                   "aliases": [], "links": [{"collection": "contacts", "source_key": "directory",
                                             "source_record_keys": ["ns"]}]},
                  {"id": "cc", "name": "Counseling Center for Students", "kind": "office",
                   "aliases": [], "links": [{"collection": "contacts", "source_key": "directory",
                                             "source_record_keys": ["cc"]}]}],
        contacts=[{"id": "source-ns", "source_key": "directory", "source_record_key": "ns",
                   "name": "NS", "phone": "(201) 555-1111", "canonical_url": "https://example.edu/ns",
                   "collected_at": NOW, "freshness_sla_hours": 24},
                  {"id": "source-cc", "source_key": "directory", "source_record_key": "cc",
                   "name": "CC", "phone": "(201) 555-7522", "canonical_url": "https://example.edu/cc",
                   "collected_at": NOW, "freshness_sla_hours": 24}],
        now=lambda: NOW)
    result = answer(ScriptedGateway(finish("safety", situation="self_harm")), service=near)
    assert result.body["answer"] == SAFETY_TEXTS["self_harm"]  # No near name stands in.
    assert result.body["citations"] == []
    assert result.trace is not None
    assert [e["status"] for e in result.trace["lookups"]] == ["not_found", "not_found"]


def test_campus_numbers_still_come_when_the_models_own_lookup_left_out_the_phones() -> None:
    from rockygpt_brain.engine import HELP_TEXT

    email_only = completion("graph_lookup", {"requests": [
        {"query": "Public Safety (Emergency)", "fields": ["email"]}]})
    result = answer(ScriptedGateway(email_only, finish("safety", situation="danger")),
                    service=campus_help_facts(),
                    messages=[{"role": "user", "content": "someone is following me"}])
    assert HELP_TEXT in result.body["answer"] and "+12015556666" in result.body["answer"]
    phones = completion("graph_lookup", {"requests": [
        {"query": "Public Safety (Emergency)", "fields": ["email", "phones"]}]})
    again = answer(ScriptedGateway(phones, finish("safety", situation="danger")),
                   service=campus_help_facts(),
                   messages=[{"role": "user", "content": "someone is following me"}])
    assert again.body["answer"].count("+12015556666") == 1  # Already shown, not repeated.


def test_a_refusal_after_a_lookup_does_not_claim_the_office_handles_the_refused_part() -> None:
    from rockygpt_brain.engine import UNSUPPORTED_AFTER_LOOKUP_MESSAGE

    assert "If the office above handles it" in UNSUPPORTED_AFTER_LOOKUP_MESSAGE
    assert "best way to ask the office directly" not in UNSUPPORTED_AFTER_LOOKUP_MESSAGE


def test_a_recalled_quote_keeps_published_text_that_looks_like_markup() -> None:
    from rockygpt_brain.answers import literal, readable_quote

    published = "Call [the desk](tel:555) or use **bold** text"
    earlier = f"**Registrar**\n\nNote: {literal(published)} [source](https://example.edu/x)"
    plain = readable_quote(earlier, "assistant")
    assert plain == f"Registrar\n\nNote: {published} source"
