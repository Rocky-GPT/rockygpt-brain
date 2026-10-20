"""Runtime trust boundaries, independent of model wording and paid providers."""

import asyncio
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

import pytest

from rockygpt_brain.boundary import SAFETY_MESSAGE, safety_text
from rockygpt_brain.context import build_context
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
    assert "Offices: I have no published information about this" in result.body["answer"]


@pytest.mark.parametrize(("kind", "phrase"), [
    ("greeting", "shuttle timetable, and dining hours and menus"),
    ("thanks", "You're welcome"),
    ("okay", "call 911, or call or text 988"),
    ("about", "I can't see your personal student records"),
])
def test_small_talk_gets_a_fixed_server_written_reply_not_an_error(kind: str, phrase: str) -> None:
    # "okay" answers an earlier emergency, so the conversation holds the Brain's own safety reply.
    earlier = ([{"role": "user", "content": "my friend is not breathing"},
                {"role": "assistant", "content": SAFETY_MESSAGE}] if kind == "okay" else [])
    result = answer(ScriptedGateway(finish(kind)),
                    messages=[*earlier, {"role": "user", "content": "hey"}])
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


def test_a_safety_part_paired_with_its_message_still_gets_the_safety_text() -> None:
    # "i took way too many pills and feel really sick" once came back with message_index 0 on
    # its safety part and the student got a 502 instead of the emergency numbers (2026-10-15).
    result = answer(ScriptedGateway(finish("safety", message_index=0)))
    assert result.status_code == 200
    assert result.body["answer"].startswith(SAFETY_MESSAGE)


def test_a_malformed_part_beside_a_safety_part_never_loses_the_safety_text() -> None:
    both = completion("finish", {"parts": [
        {"kind": "safety", "message_index": None, "situation": "medical"},
        {"kind": "recall", "message_index": 7, "situation": None}]})
    result = answer(ScriptedGateway(both))
    assert result.status_code == 200
    assert result.body["answer"].startswith(safety_text("medical"))


def test_a_malformed_part_without_a_safety_part_is_still_refused() -> None:
    result = answer(ScriptedGateway(finish("account_limit", message_index=0)))
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


def two_offices_and_a_person() -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[
            {"id": "b", "name": "ID Card Room", "kind": "office", "aliases": ["Husky Card"],
             "links": []},
            {"id": "a", "name": "Registrar", "kind": "office", "aliases": [], "links": []},
            {"id": "p", "name": "A Person", "kind": "person", "aliases": [], "links": []},
        ],
        contacts=[], now=lambda: NOW)


def test_the_model_is_shown_the_published_offices_and_the_names_each_goes_by() -> None:
    gateway = ScriptedGateway(finish("greeting"))
    answer(gateway, service=two_offices_and_a_person())
    state = json.loads(gateway.inputs[0][1]["content"])
    assert tuple(state) == MODEL_INPUT_KEYS
    root = state["graph_root"]
    assert root["id"] == "ramapo" and [child["id"] for child in root["children"]] == [
        "offices", "dining", "shuttle", "topics"]
    # Offices only, sorted, each with its other names: no person, no id, no record.
    assert state["published_offices"] == [
        {"name": "ID Card Room", "also_called": ["Husky Card"]}, {"name": "Registrar"}]


def test_okay_is_kept_only_when_an_emergency_reply_is_in_the_conversation() -> None:
    def run(history: list[dict[str, str]], latest: str = "its fine now, false alarm") -> str:
        messages = [*history, {"role": "user", "content": latest}]
        result = answer(ScriptedGateway(finish("okay")), messages=messages)
        text: str = result.body["answer"]
        return text

    after = run([{"role": "user", "content": "my friend is not breathing"},
                 {"role": "assistant", "content": SAFETY_MESSAGE}])
    assert "thanks for letting me know" in after
    # With no emergency in the conversation it is a misfire, and the student is asked instead.
    stray = run([{"role": "user", "content": "is there a trading lab"},
                 {"role": "assistant", "content": "The Trading Lab is in the business school."}],
                "can u just give me their contact info")
    assert "thanks for letting me know" not in stray


def test_a_clarification_names_only_published_offices_and_the_code_writes_the_question() -> None:
    service = two_offices_and_a_person()
    result = answer(ScriptedGateway(finish(
        "clarification", offices=["registrar", "Husky Card", "ID Card Room", "Made Up Office"])),
        service=service)
    # An alias one office has resolves to that office, a repeat merges, an unknown name is dropped.
    assert result.body["answer"] == "Which office do you mean: Registrar, ID Card Room?"
    assert result.body["status"] == "clarification"


def test_a_clarification_with_fewer_than_two_published_offices_is_the_generic_question() -> None:
    from rockygpt_brain.engine import CLARIFICATION_MESSAGE
    for offered in ([], ["Registrar"], ["Registrar", "Made Up Office"]):
        result = answer(ScriptedGateway(finish("clarification", offices=offered)),
                        service=two_offices_and_a_person())
        assert result.body["answer"] == CLARIFICATION_MESSAGE


def test_known_names_leave_out_an_alias_that_two_offices_share() -> None:
    from rockygpt_brain.engine import known_office_names, named_options
    listing = {"offices": [{"name": "A Office", "aliases": ["Shared", "Only A"]},
                           {"name": "B Office", "aliases": ["Shared"]}]}
    known = known_office_names(listing)
    assert known == {"a office": "A Office", "only a": "A Office", "b office": "B Office"}
    assert named_options(["SHARED", "only  a", "b office"], known) == ["A Office", "B Office"]
    assert known_office_names(None) == {}


def test_a_cut_off_or_oversized_office_list_is_not_shown_as_the_whole_list() -> None:
    from rockygpt_brain.engine import MAX_OFFICE_LIST_BYTES, published_offices

    def listing(offices: list[dict[str, Any]], truncated: bool = False) -> dict[str, Any]:
        return {"offices": offices, "truncated": truncated}

    short = [{"name": "Registrar", "aliases": ["Records"]}]
    assert published_offices(listing(short, truncated=True)) is None
    assert published_offices(listing(short)) == [{"name": "Registrar", "also_called": ["Records"]}]
    # Too long with the other names: names only. Too long even so: not sent.
    names = [{"name": f"Office {n:03d}", "aliases": ["a long nickname " * 8]} for n in range(120)]
    cut = published_offices(listing(names))
    assert cut is not None and all(list(o) == ["name"] for o in cut)
    huge = [{"name": "Office " + "x" * 200 + str(n), "aliases": []}
            for n in range(MAX_OFFICE_LIST_BYTES // 100)]
    assert published_offices(listing(huge)) is None


def test_a_turn_goes_on_without_the_list_when_it_cannot_be_read() -> None:
    service = two_offices_and_a_person()

    def unreadable(**_: Any) -> dict[str, Any]:
        raise EvidenceUnavailable("the list cannot be read")

    service.list_offices = unreadable  # type: ignore[method-assign]
    gateway = ScriptedGateway(finish("greeting"))
    result = answer(gateway, service=service)
    assert result.status_code == 200
    assert json.loads(gateway.inputs[0][1]["content"])["published_offices"] is None


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
    assert lookup["arguments"] == {
        "query": "Registrar", "fields": ["email"], "day": None, "phone_kind": None,
        "meal": None, "diet": None, "items": [], "with_allergens": [], "without_allergens": [],
        "details": [], "about": None, "page_terms": []}
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
    # Seven matched and five are shown, so the list is cut off and says so.
    assert entry["truncated"] is True


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
    question = "what is the registrar email and can I book an appointment"
    words = "book an appointment"
    result = answer(ScriptedGateway(LOOKUP, finish("unsupported", student_words=words)),
                    messages=[{"role": "user", "content": question}])
    assert "published@example.edu" in result.body["answer"]
    assert "I don't have verified information about that part" in result.body["answer"]
    assert result.body["status"] == "partial"
    assert result.trace is not None and result.trace["unsupported_about"] == [words]


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

    messages = [{"role": "user", "content": "can I book an appointment with the registrar"}]
    quote = "book an appointment"
    with_office = answer(ScriptedGateway(LOOKUP, finish("unsupported", student_words=quote)),
                         messages=messages)
    assert with_office.body["answer"].endswith(UNSUPPORTED_AFTER_LOOKUP_MESSAGE)
    # With no office facts above, a refusal stands even with no words to quote.
    alone = answer(ScriptedGateway(finish("unsupported")), messages=messages)
    assert alone.body["answer"] == UNSUPPORTED_MESSAGE
    with_words = answer(ScriptedGateway(finish("unsupported", student_words=quote)),
                        messages=messages)
    assert with_words.body["answer"] == UNSUPPORTED_MESSAGE
    assert "posted shuttle timetable" in UNSUPPORTED_MESSAGE
    own = answer(ScriptedGateway(LOOKUP, finish("account_limit")), messages=messages)
    assert own.body["answer"].endswith(CAPABILITY_AFTER_LOOKUP_MESSAGE)
    assert answer(ScriptedGateway(finish("account_limit")),
                  messages=messages).body["answer"] == CAPABILITY_MESSAGE
    # An office lookup that found nothing shows no details, so nothing is "above".
    nothing = answer(ScriptedGateway(student_lookup("Cafeteria"), finish("unsupported")))
    assert UNSUPPORTED_AFTER_LOOKUP_MESSAGE not in nothing.body["answer"]


def test_an_unsupported_part_beside_facts_needs_the_students_own_words() -> None:
    """Office facts are shown above, so the model must quote what it cannot answer. A note that
    mentions appointments is not a question about them."""
    def run(question: str, **changes: Any) -> ChatResult:
        return answer(ScriptedGateway(LOOKUP, finish("unsupported", **changes)),
                      messages=[{"role": "user", "content": question}])

    asked = "contact info for the registrar"
    for changes in ({}, {"student_words": None},
                    {"student_words": "make an appointment"},  # not their words
                    {"student_words": "contact info"},  # a field the lookup answers
                    {"student_words": "contact info for the registrar"}):  # field and office name
        dropped = run(asked, **changes)
        assert "I don't have verified information" not in dropped.body["answer"], changes
        assert "published@example.edu" in dropped.body["answer"]
        assert dropped.body["status"] == "answered"
        assert dropped.trace is not None and dropped.trace["unsupported_dropped"] == [
            changes.get("student_words")]
    # A fax is a phone line the lookup reads (phone_kind fax), so it is answered, never unsupported.
    fax = run("registrar fax number", student_words="fax number")
    assert "I don't have verified information" not in fax.body["answer"]
    assert fax.trace is not None and fax.trace["unsupported_dropped"] == ["fax number"]
    # Words in the message the lookup did not answer keep the part, whatever the casing.
    kept = run("Registrar email, and what is the REFUND policy?", student_words="Refund Policy")
    assert "I don't have verified information about that part" in kept.body["answer"]
    assert kept.body["status"] == "partial"
    assert kept.trace is not None and kept.trace["unsupported_about"] == ["Refund Policy"]
    # A model that pairs the words with the message they are in sets message_index on the part;
    # that is not an invalid response (it is for a recall only), so it is ignored.
    stray = run("Registrar email and the refund policy?", student_words="refund policy",
                message_index=0)
    assert stray.status_code == 200
    assert "I don't have verified information about that part" in stray.body["answer"]


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
    assert "Phones: I have no published information about this" in text
    assert result.body["status"] == "partial"


def test_hours_for_an_office_without_a_schedule_say_not_published() -> None:
    asked = completion("graph_lookup", {"requests": [
        {"query": "Registrar", "fields": ["email", "hours"]}]})
    result = answer(ScriptedGateway(asked, finish("unsupported")),
                    messages=[{"role": "user", "content": "registrar hours"}])
    assert "Hours: I have no published information about this." in result.body["answer"]
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


def test_a_not_911_message_is_answered_beside_the_emergency_text() -> None:
    from rockygpt_brain.boundary import check

    def floor(text: str) -> Any:
        request = ChatRequest.model_validate({"messages": [{"role": "user", "content": text}]})
        return check(intake(request, now=NOW))

    # Only a mention of 911 that says it is not one: the emergency text stays, the answer follows.
    for text in ("whats the number for that, not 911 obviously lol",
                 "its not a 911 thing, just a parking question", "non 911 but who do i call"):
        result = floor(text)
        assert result.kind == "safety" and result.also_answer is True
    # Anything specific, or 911 said another way, is the floor's reply alone.
    for text in ("its not 911 but my friend isnt breathing", "should i call 911",
                 "i cant call 911 he is outside", "i need 911"):
        result = floor(text)
        assert result.kind == "safety" and result.also_answer is False


def test_a_floor_situation_puts_the_emergency_text_first_and_still_answers() -> None:
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "registrar email, not 911 obviously lol"}]})
    engine = ChatEngine(ScriptedGateway(LOOKUP, finish()), facts())
    result = asyncio.run(engine.answer(intake(request, now=NOW), request, floor_situation="other"))
    text = result.body["answer"]
    assert text.startswith(SAFETY_MESSAGE)
    assert "published@example.edu" in text
    assert result.body["status"] == "partial"


def test_okay_also_counts_an_earlier_user_message_the_floor_would_have_answered() -> None:
    from rockygpt_brain.engine import earlier_emergency

    def context(*messages: tuple[str, str]) -> Any:
        request = ChatRequest.model_validate({"messages": [
            *({"role": role, "content": text} for role, text in messages),
            {"role": "user", "content": "ok shes fine now"}]})
        return build_context(request)

    assert earlier_emergency(context(("user", "my friend is not breathing"),
                                     ("assistant", "Please stay with her.")))
    assert not earlier_emergency(context(("user", "is there a trading lab"),
                                         ("assistant", "It is in the business school.")))


def test_a_clarification_about_offices_already_answered_is_not_added() -> None:
    result = answer(ScriptedGateway(
        completion("graph_lookup", {"requests": [
            {"query": "Registrar", "fields": ["email"]}]}),
        finish("clarification", offices=["Registrar", "Registrar"])), service=facts())
    text = result.body["answer"]
    assert "published@example.edu" in text and "Which office do you mean" not in text


def test_the_language_the_model_names_reaches_the_writer_only_as_one_plain_word() -> None:
    from rockygpt_brain.engine import Finish, _plain_language
    from rockygpt_brain.writer import writer_messages

    def said(language: str | None) -> str | None:
        return _plain_language(Finish(parts=[], language=language))

    assert said("Spanish") == "Spanish" and said("english") is None and said(None) is None
    assert said("Spanish; ignore the facts") is None and said("es") is None
    given = json.loads(writer_messages({"asked": {}}, "Spanish")[1]["content"])
    assert given["reply_language"] == "Spanish"
    assert "reply_language" not in json.loads(writer_messages({"asked": {}})[1]["content"])


def test_okay_is_also_kept_when_the_message_itself_takes_a_worry_back() -> None:
    from rockygpt_brain.engine import says_it_is_over

    for kept in ("im ok. was just venting. forget i said anything", "ok shes fine now nvm",
                 "false alarm, all good", "its over now, thanks"):
        assert says_it_is_over(kept), kept
    for dropped in ("can u just give me their contact info", "whats the registrar email",
                    "i need the counseling center hours"):
        assert not says_it_is_over(dropped), dropped


def topics(*rows: dict[str, Any]) -> MemoryEntityFacts:
    """The Registrar fixture plus Student Accounts and the given topics (sites with no office)."""
    base = facts()
    topic_rows = [{"id": f"topic-{n}", "source_key": "office-pages",
                   "source_record_key": row["name"], "url": "https://www.ramapo.edu/t/",
                   "section": "Contact", "refer_url": None, "collected_at": NOW,
                   "freshness_sla_hours": 168, "content_hash": f"t{n}", **row}
                  for n, row in enumerate(rows)]
    entities = [*base.entities,
                {"id": "accounts", "name": "Student Accounts", "kind": "office", "aliases": [],
                 "links": []},
                *({"id": f"topic-{row['name']}", "name": row["name"], "kind": "topic",
                   "aliases": [], "links": [{"collection": "campus_topics",
                                             "source_key": "office-pages",
                                             "source_record_keys": [row["name"]]}]}
                  for row in topic_rows)]
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=entities, contacts=base.contacts,
                             records={"campus_topics": topic_rows}, now=lambda: NOW)


def topic_lookup(query: str) -> Completion:
    return completion("graph_lookup", {"requests": [{"query": query, "fields": ["email"]}]})


def test_a_topic_with_one_office_answers_for_that_office_and_says_why() -> None:
    service = topics({"name": "Summer Session", "status": "current", "handled_by": ["Registrar"],
                      "quote": "Contact the Registrar's office"})
    result = answer(ScriptedGateway(topic_lookup("Summer Session"), finish()), service=service,
                    messages=[{"role": "user", "content": "summer session email?"}])
    text = result.body["answer"]
    assert text.startswith("For Summer Session, its page sends you to Registrar.")
    assert "published@example.edu" in text
    assert "https://www.ramapo.edu/t/" in [c["url"] for c in result.body["citations"]]


def test_a_topic_with_several_offices_offers_them_to_choose_from() -> None:
    service = topics({"name": "Scholarships", "status": "current",
                      "handled_by": ["Student Accounts", "Registrar"],
                      "quote": "Student Accounts ... Registrar"})
    result = answer(ScriptedGateway(topic_lookup("Scholarships"), finish()), service=service,
                    messages=[{"role": "user", "content": "who do i ask about scholarships"}])
    assert result.body["answer"] == ("For Scholarships, its page sends you to these offices: "
                                     "Student Accounts, Registrar. Which one do you need?")
    assert result.body["status"] == "clarification"


def test_a_retired_topic_says_it_is_gone_in_its_pages_words() -> None:
    service = topics({"name": "Small Business Development Center", "status": "retired",
                      "handled_by": [], "refer_url": "https://njsbdc.com/",
                      "quote": "The Bergen County SBDC is no longer headquartered at Ramapo"})
    result = answer(ScriptedGateway(topic_lookup("Small Business Development Center"), finish()),
                    service=service, messages=[{"role": "user", "content": "sbdc contact"}])
    assert result.body["answer"].startswith(
        "Small Business Development Center is no longer at Ramapo College. Its page says: "
        "\"The Bergen County SBDC is no longer headquartered at Ramapo\" It points to "
        "https://njsbdc.com/.")


def test_an_office_named_exactly_wins_over_a_topic_and_a_topic_needs_its_exact_name() -> None:
    service = topics({"name": "Registrar Help", "status": "current",
                      "handled_by": ["Student Accounts", "Registrar"], "quote": "x"})
    result = answer(ScriptedGateway(topic_lookup("Registrar"), finish()), service=service)
    assert "published@example.edu" in result.body["answer"]
    assert "its page sends you" not in result.body["answer"]


def test_a_topic_naming_an_office_the_release_lacks_is_unavailable_not_guessed() -> None:
    service = topics({"name": "Scholarships", "status": "current",
                      "handled_by": ["Bursar"], "quote": "x"})
    result = answer(ScriptedGateway(topic_lookup("Scholarships"), finish()), service=service)
    assert result.status_code == 503


def test_the_model_sees_topics_marked_in_the_office_list() -> None:
    service = topics({"name": "Scholarships", "status": "current",
                      "handled_by": ["Registrar"], "quote": "x"})
    gateway = ScriptedGateway(finish())
    answer(gateway, service=service, messages=[{"role": "user", "content": "hi"}])
    state = json.loads(gateway.inputs[0][1]["content"])
    assert {"name": "Scholarships", "topic": True} in state["published_offices"]


def test_the_packet_carries_the_topic_and_its_page_as_a_notice() -> None:
    service = topics({"name": "Scholarships", "status": "current",
                      "handled_by": ["Student Accounts", "Registrar"], "quote": "both offices"})
    request = ChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "scholarships help"}]})
    engine = ChatEngine(ScriptedGateway(topic_lookup("Scholarships"), finish()), service,
                        output="json")
    packet = asyncio.run(engine.answer(intake(request, now=NOW), request)).body["facts"]
    assert packet["status"] == "ambiguous"
    assert [c["name"] for c in packet["ambiguities"][0]["candidates"]] == ["Student Accounts",
                                                                          "Registrar"]
    notice = next(n for n in packet["notices"] if n["type"] == "topic")
    assert notice["page"] == "https://www.ramapo.edu/t/" and notice["quote"] == "both offices"
    assert notice["approved_text"].startswith("For Scholarships, its page sends you to")


def test_the_graph_lists_each_topic_with_the_offices_its_page_names() -> None:
    from rockygpt_brain.retrieval.campus_graph import CampusGraph
    service = topics({"name": "Scholarships", "status": "current",
                      "handled_by": ["Student Accounts", "Registrar"], "quote": "both offices"})
    branch = CampusGraph(service).inspect("topics", NOW)
    assert [(c["label"], c["kind"]) for c in branch["children"]] == [("Scholarships", "topic")]
    node = CampusGraph(service).inspect(branch["children"][0]["id"], NOW)
    assert [o["name"] for o in node["topic"]["offices"]] == ["Student Accounts", "Registrar"]
    assert node["topic"]["quote"] == "both offices"


def test_a_venue_is_listed_with_its_building_and_the_offices_published_there() -> None:
    def contact(key: str, name: str, office: str) -> dict[str, Any]:
        return {"id": f"c-{key}", "source_key": "directory", "source_record_key": key,
                "name": name, "office": office, "canonical_url": "https://example.edu/x",
                "collected_at": NOW, "freshness_sla_hours": 24}

    def entity(key: str, name: str, kind: str) -> dict[str, Any]:
        return {"id": key, "name": name, "kind": kind, "aliases": [], "links": [
            {"collection": "contacts", "source_key": "directory", "source_record_keys": [key]}]}

    service = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[entity("cafe", "Common Grounds", "venue"), entity("library", "Library", "office"),
                  entity("registrar", "Registrar", "office")],
        contacts=[contact("cafe", "Common Grounds", "Learning Commons"),
                  contact("library", "Library", "Learning Commons"),
                  contact("registrar", "Registrar", "D-101")], now=lambda: NOW)
    gateway = ScriptedGateway(finish())
    answer(gateway, service=service, messages=[{"role": "user", "content": "hi"}])
    state = json.loads(gateway.inputs[0][1]["content"])
    assert state["published_venues"] == [{"name": "Common Grounds", "publishes": [],
                                          "in": ["Learning Commons"],
                                          "with": ["Library"]}]


def budget_office() -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "budget", "name": "Budget", "kind": "office", "aliases": [], "links": [
            {"collection": "contacts", "source_key": "directory", "source_record_keys": ["b"]}]}],
        contacts=[{"id": "c-b", "source_key": "directory", "source_record_key": "b",
                   "name": "Budget",
                   "email": "budget@example.edu", "office": "D-211", "preferred_contact": "email",
                   "prefers_email": True, "canonical_url": "https://example.edu/budget",
                   "collected_at": NOW, "freshness_sla_hours": 24}], now=lambda: NOW)


def test_a_preferred_way_to_reach_an_office_comes_with_its_address() -> None:
    ask = completion("graph_lookup", {"requests": [
        {"query": "Budget", "fields": ["preferred_contact"]}]})
    result = answer(ScriptedGateway(ask, finish()), service=budget_office(),
                    messages=[{"role": "user", "content": "best way to reach the budget office"}])
    assert "budget@example.edu" in result.body["answer"]


def test_the_contact_fallback_is_only_offered_beside_a_shown_email_or_phone() -> None:
    where = completion("graph_lookup", {"requests": [{"query": "Budget", "fields": ["offices"]}]})
    unsupported = completion("finish", {"parts": [
        {"kind": "unsupported", "message_index": None, "situation": None,
         "student_words": "sabbatical rules"}]})
    result = answer(ScriptedGateway(where, unsupported), service=budget_office(),
                    messages=[{"role": "user",
                               "content": "where is budget and the sabbatical rules"}])
    assert "D-211" in result.body["answer"]
    assert "its contact details are the best way to ask" not in result.body["answer"]


def test_an_allergy_part_beside_a_menu_is_not_called_unanswerable() -> None:
    from rockygpt_brain.engine import _asked_words
    message = "does the pasta have any nuts or dairy, i have allergies"
    assert _asked_words("any nuts or dairy", message, {"Birch Tree Inn"}, menu_read=True) is None
    assert _asked_words("any nuts or dairy", message, {"Registrar"}) == "any nuts or dairy"
    asked = "do the menus list allergens? i have a severe peanut allergy and need to know"
    assert _asked_words("do the menus list allergens", asked, {"Birch Tree Inn"},
                        menu_read=True) is None
    whole = "does the pasta at the dining hall today have any nuts or dairy"
    assert _asked_words(whole, message, {"Dining Hall", "pasta"}, menu_read=True) is None


def marketing_office() -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "mkt", "name": "Marketing", "kind": "office", "aliases": [], "links": [
            {"collection": "contacts", "source_key": "directory", "source_record_keys": ["m"]}]}],
        contacts=[{"id": "c-m", "source_key": "directory", "source_record_key": "m",
                   "name": "Marketing", "email": "marketing@example.edu",
                   "contact_note": "Media inquiries: news@example.edu",
                   "canonical_url": "https://example.edu/marketing",
                   "collected_at": NOW, "freshness_sla_hours": 24}], now=lambda: NOW)


def test_a_question_naming_a_purpose_its_contact_note_covers_reads_the_note() -> None:
    ask = completion("graph_lookup", {"requests": [{"query": "Marketing", "fields": ["email"]}]})
    press = answer(ScriptedGateway(ask, finish()), service=marketing_office(), messages=[
        {"role": "user", "content": "who handles media inquiries and how do I reach them"}])
    assert "news@example.edu" in press.body["answer"]
    plain = answer(ScriptedGateway(ask, finish()), service=marketing_office(),
                   messages=[{"role": "user", "content": "marketing email please"}])
    assert "news@example.edu" not in plain.body["answer"]


def id_card_room(note: str) -> MemoryEntityFacts:
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "id", "name": "ID Card Room", "kind": "office",
                   "aliases": ["New ID Card"], "links": [
                       {"collection": "contacts", "source_key": "directory",
                        "source_record_keys": ["i"]}]}],
        contacts=[{"id": "c-i", "source_key": "directory", "source_record_key": "i",
                   "name": "ID Card Room", "office": "C-101", "contact_note": note,
                   "canonical_url": "https://example.edu/id", "collected_at": NOW,
                   "freshness_sla_hours": 24}], now=lambda: NOW)


def test_where_to_go_reads_a_contact_note_that_says_to_make_an_appointment() -> None:
    ask = completion("graph_lookup",
                     {"requests": [{"query": "New ID Card", "fields": ["offices"]}]})
    message = [{"role": "user", "content": "where do i go to get my student ID card made"}]
    note = ("In order to get a new identification card, please contact the ID room at "
            "idroom@example.edu and make an appointment.")
    visit = answer(ScriptedGateway(ask, finish()), service=id_card_room(note), messages=message)
    assert "C-101" in visit.body["answer"] and "idroom@example.edu" in visit.body["answer"]
    # Said in the office's own words, so a writer cannot leave the appointment out.
    request = ChatRequest.model_validate({"messages": message})
    engine = ChatEngine(ScriptedGateway(ask, finish()), id_card_room(note),  # type: ignore[arg-type]
                        output="json")
    packet = asyncio.run(engine.answer(intake(request, now=NOW), request)).body["facts"]
    assert [n["approved_text"] for n in packet["notices"]
            if n["type"] == "appointment_note"] == [note]
    # A note with nothing about appointments is not added beside the room.
    other = answer(ScriptedGateway(ask, finish()), messages=message,
                   service=id_card_room("Lost cards: lost@example.edu"))
    assert "C-101" in other.body["answer"] and "lost@example.edu" not in other.body["answer"]


def test_a_question_asking_where_is_answered_with_the_room() -> None:
    ask = completion("graph_lookup", {"requests": [{"query": "ID Card Room", "fields": ["email"]}]})
    room = id_card_room("Lost cards: lost@example.edu")
    where = answer(ScriptedGateway(ask, finish()), service=room, messages=[
        {"role": "user", "content": "where is the id room and whats their email"}])
    assert "C-101" in where.body["answer"]
    plain = answer(ScriptedGateway(ask, finish()), service=room,
                   messages=[{"role": "user", "content": "id room email please"}])
    assert "C-101" not in plain.body["answer"]


def test_a_contact_note_sentence_that_speaks_to_the_question_is_said_in_its_own_words() -> None:
    note = ("For questions, call the Dining Services Office. Students who are ill or injured "
            "may request a boxed meal through Dining Services. There is a specific process for "
            "making special requests for medical, religious, cultural or other reasons. Students "
            "with such requests should inquire at the Dining Services Office, across from the "
            "Atrium. To request a change to a meal plan, email mealplan@example.edu.")
    dining = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "dining", "name": "Dining Services", "kind": "office", "aliases": [],
                   "links": [{"collection": "contacts", "source_key": "directory",
                              "source_record_keys": ["d"]}]}],
        contacts=[{"id": "c-d", "source_key": "directory", "source_record_key": "d",
                   "name": "Dining Services", "email": "dining@example.edu",
                   "contact_note": note, "canonical_url": "https://example.edu/dining",
                   "collected_at": NOW, "freshness_sla_hours": 24}], now=lambda: NOW)
    ask = completion("graph_lookup", {"requests": [
        {"query": "Dining Services", "fields": ["email", "contact_note"]}]})

    def notes(question: str) -> list[str]:
        request = ChatRequest.model_validate({"messages": [{"role": "user", "content": question}]})
        engine = ChatEngine(ScriptedGateway(ask, finish()), dining,  # type: ignore[arg-type]
                            output="json")
        packet = asyncio.run(engine.answer(intake(request, now=NOW), request)).body["facts"]
        return [n["approved_text"] for n in packet["notices"] if n["type"] == "note_answer"]

    # A celiac accommodation is one of the note's special requests for medical reasons.
    assert notes("who do i talk to about a dietary accommodation for celiac") == [
        "There is a specific process for making special requests for medical, religious, "
        "cultural or other reasons. Students with such requests should inquire at the Dining "
        "Services Office, across from the Atrium."]
    # A question that only names the office is answered by its fields, not the note.
    assert notes("dining services email") == []


def test_a_writer_reply_that_already_says_a_notice_in_its_own_words_is_not_repeated() -> None:
    from rockygpt_brain.engine import already_said
    note = ("In order to get a new identification card, please contact the ID room at "
            "idroom@example.edu and make an appointment.")
    assert already_said(note, "C-101. To get a new identification card, contact the ID room at "
                              "idroom@example.edu and make an appointment.")
    assert not already_said(note, "The ID Card Room is in C-101.")


def res_life() -> MemoryEntityFacts:
    note = ("Residents may contact the Resident Assistants during posted hours. The Residence Life "
            "Mailroom: mailroom@example.edu Mackin Hall: mackin@example.edu")
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "rl", "name": "Housing & Residence Life", "kind": "office",
                   "aliases": ["Res Life"], "links": [
                       {"collection": "contacts", "source_key": "directory",
                        "source_record_keys": ["r"]}]}],
        contacts=[{"id": "c-r", "source_key": "directory", "source_record_key": "r",
                   "name": "Housing & Residence Life", "email": "reslife@example.edu",
                   "contact_note": note, "canonical_url": "https://example.edu/reslife",
                   "collected_at": NOW, "freshness_sla_hours": 24}], now=lambda: NOW)


def test_a_service_an_office_lists_as_a_labelled_contact_finds_that_office() -> None:
    from rockygpt_brain.engine import service_labels
    assert service_labels([
        {"key": "contact_note", "status": "known", "values": [{"value": (
            "Call us. The Residence Life Mailroom: orl@example.edu Early Arrival: e@example.edu "
            "Desk: (201) 555-0100")}]},
        {"key": "phones", "status": "known", "values": [{"value": [
            {"number": "+12015550100"}, {"type": "Mackin Hall", "number": "+12015550101"}]}]},
    ]) == ["The Residence Life Mailroom", "Early Arrival", "Desk", "Mackin Hall"]
    ask = completion("graph_lookup", {"requests": [
        {"query": "post office / mailroom", "fields": ["offices", "email"]}]})
    found = answer(ScriptedGateway(ask, finish()), service=res_life(), messages=[
        {"role": "user", "content": "where is the mailroom and what's the email"}])
    assert "mailroom@example.edu" in found.body["answer"]
    assert found.trace is not None and found.trace["lookups"][0]["resolvedBy"] == "service"
    # One short word is in too many labels to name a service: "hall" alone is not Mackin Hall.
    hall = completion("graph_lookup", {"requests": [{"query": "hall", "fields": ["email"]}]})
    vague = answer(ScriptedGateway(hall, finish()), service=res_life(), messages=[
        {"role": "user", "content": "hall email"}])
    assert "mackin@example.edu" not in vague.body["answer"]
    # Words that no label has are still not found.
    nothing = completion("graph_lookup", {"requests": [
        {"query": "bursar", "fields": ["email"]}]})
    missing = answer(ScriptedGateway(nothing, finish()), service=res_life(), messages=[
        {"role": "user", "content": "bursar email"}])
    assert "mailroom@example.edu" not in missing.body["answer"]


def registrar_with_pages() -> MemoryEntityFacts:
    def page(n: int, url: str, sections: list[dict[str, str]]) -> dict[str, Any]:
        return {"id": f"page-{n}", "source_key": "office-pages", "source_record_key": url,
                "site_folder": "registrar", "url": f"https://{url}", "sections": sections,
                "collected_at": NOW, "freshness_sla_hours": 168, "content_hash": f"h{n}"}
    pages = [
        page(1, "www.ramapo.edu/registrar/transcripts/", [
            {"heading": "How do I request an official transcript?",
             "text": "Order official transcripts online through Parchment. Each costs $10."},
            {"heading": "Unofficial transcripts", "text": "Print one from Web Self-Service."}]),
        page(2, "www.ramapo.edu/registrar/forms/", [
            {"heading": "Change of Major", "text": "Submit the change of major form."}]),
    ]
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "registrar", "name": "Registrar", "kind": "office", "aliases": [],
                   "links": [{"collection": "contacts", "source_key": "directory",
                              "source_record_keys": ["registrar"]},
                             {"collection": "office_sections", "source_key": "office-pages",
                              "source_record_keys": [p["source_record_key"] for p in pages]}]}],
        contacts=[{"id": "source-registrar", "source_key": "directory",
                   "source_record_key": "registrar", "name": "Registrar",
                   "email": "registrar@example.edu", "canonical_url": "https://example.edu/reg",
                   "collected_at": NOW, "freshness_sla_hours": 24}],
        records={"office_sections": pages}, now=lambda: NOW)


def test_a_procedure_is_read_from_the_sections_of_the_offices_own_pages() -> None:
    from rockygpt_brain.retrieval.entity_facts import procedures_property
    service = registrar_with_pages()
    facts = service.get_office_facts("registrar", ["procedures"], "release-1",
                                     identity_hash="identities-1", about="request a transcript")
    (prop,) = facts["properties"]
    assert prop["status"] == "known"
    first = prop["values"][0]["value"]
    assert first["heading"] == "How do I request an official transcript?"
    assert first["page"] == "https://www.ramapo.edu/registrar/transcripts/"
    assert "Parchment" in first["text"]
    # Each section cites its own page.
    (source,) = [s for s in facts["sources"] if s["id"] == "page-1"]
    assert source["citation_urls"] == ["https://www.ramapo.edu/registrar/transcripts/"]
    # Words no section has are unknown, never not published; no words reads nothing.
    none = service.get_office_facts("registrar", ["procedures"], "release-1",
                                    identity_hash="identities-1", about="parking permit")
    assert none["properties"][0]["status"] == "unknown"
    assert procedures_property([], NOW, "transcript")[0]["status"] == "unknown"
    # A plain read of the office never reads its pages.
    plain = service.get_office_facts("registrar", None, "release-1", identity_hash="identities-1")
    assert all(p["key"] != "procedures" for p in plain["properties"])


def test_a_section_for_a_term_that_is_over_is_marked_and_ranked_below_todays() -> None:
    from rockygpt_brain.retrieval.entity_facts import past_term, procedures_property
    today = date(2026, 10, 19)
    assert past_term("Spring 2026 Check-Out", today) == "Spring 2026"
    assert past_term("Fall 2026 move-in and Spring 2026 notes", today) is None
    assert past_term("Spring 2027 housing", today) is None
    assert past_term("How to check out", today) is None
    # An academic year ends on August 31 of its second year.
    assert past_term("2025-2026 parking permit prices", today) == "2025-2026"
    assert past_term("2025-26 rates", today) == "2025-2026"
    assert past_term("2026-2027 parking permit prices", today) is None
    assert past_term("2025-2026 and 2026-2027 rates", today) is None
    assert past_term("call 201-684-7000", today) is None
    page = {"id": "page-1", "source_key": "office-pages",
            "source_record_key": "www.ramapo.edu/reslife/a/",
            "url": "https://www.ramapo.edu/reslife/a/", "site_folder": "reslife",
            "collected_at": NOW, "freshness_sla_hours": 168, "content_hash": "h1",
            "sections": [
                {"heading": "Spring 2026 Check-Out", "text": "Check out of your room by May 15."},
                {"heading": "Check-Out", "text": "Check out of your room with your RA."}]}
    prop, _ = procedures_property([page], NOW, "check out of my room")
    first, second = (v["value"] for v in prop["values"])
    assert first["heading"] == "Check-Out" and "past_term" not in first
    assert second["past_term"] == "Spring 2026"


def test_a_how_do_i_question_reads_the_offices_procedures_end_to_end() -> None:
    ask = completion("graph_lookup", {"requests": [
        {"query": "Registrar", "fields": ["procedures"], "about": "request a transcript"}]})
    # A model keeps the sections that answer: here the first, not the unofficial-transcript one.
    pick = completion("pick_sections", {"picks": [{"lookup": 0, "sections": [0]}]})
    result = answer(ScriptedGateway(ask, pick, finish()), service=registrar_with_pages(),
                    messages=[{"role": "user", "content": "how do i get my transcript"}])
    assert "Parchment" in result.body["answer"]
    assert "Web Self-Service" not in result.body["answer"]
    assert result.trace is not None and result.trace["procedurePicks"] == "model"
    # Picking none leaves the office's pages unknown for it rather than saying a guess.
    none = completion("pick_sections", {"picks": [{"lookup": 0, "sections": []}]})
    unpicked = answer(ScriptedGateway(ask, none, finish()), service=registrar_with_pages(),
                      messages=[{"role": "user", "content": "how do i get my transcript"}])
    assert "Parchment" not in unpicked.body["answer"]
    assert result.trace is not None
    assert result.trace["lookups"][0]["arguments"]["about"] == "request a transcript"


def test_a_long_faq_section_says_the_question_that_matches_and_short_names_count() -> None:
    from rockygpt_brain.retrieval.entity_facts import MAX_PROCEDURE_CHARS, procedures_property
    filler = "How do I visit campus? Tours run daily from the welcome center. " * 40
    faq = (filler + "Will my county college credits be accepted? We accept up to the maximum 65 "
           "transfer credits from a two-year school. " + "Is housing available? Yes. " * 20)
    assert len(faq) > MAX_PROCEDURE_CHARS
    page = {"id": "page-1", "source_key": "office-pages",
            "source_record_key": "www.ramapo.edu/undergraduate/transfer/faqs/",
            "url": "https://www.ramapo.edu/undergraduate/transfer/faqs/",
            "site_folder": "undergraduate", "collected_at": NOW, "freshness_sla_hours": 168,
            "content_hash": "h1", "sections": [{"heading": "FAQs", "text": faq},
                                               {"heading": "New ID Cards",
                                                "text": "Email the ID room for an appointment."}]}
    prop, _ = procedures_property([page], NOW, "maximum transfer credits from a county college")
    first = prop["values"][0]["value"]
    assert first["text"].startswith("Will my county college credits be accepted?")
    assert "65" in first["text"] and first["text_cut"] is True
    # "ID" in capitals names the card; "id" as a word of two letters says nothing.
    ids, _ = procedures_property([page], NOW, "get a student ID")
    assert ids["values"][0]["value"]["heading"] == "New ID Cards"


def test_a_page_with_how_to_entries_offers_them_instead_of_its_sections() -> None:
    from rockygpt_brain.retrieval.entity_facts import procedures_property
    base = {"source_key": "office-pages", "site_folder": "clubs", "collected_at": NOW,
            "freshness_sla_hours": 168, "content_hash": "h"}
    listed = {**base, "id": "page-1", "source_record_key": "www.ramapo.edu/clubs/",
              "url": "https://www.ramapo.edu/clubs/",
              "sections": [{"heading": "Expressive Activity",
                            "text": "To start a demonstration, register the activity."}],
              "howto_entries": [
                  {"task": "Start a new student club", "applies_to": "", "handled_by": "",
                   "term": "", "section": "Clubs", "steps": [
                       {"text": "Ask the Center for Student Involvement in SC-218.",
                        "quote": "Stop by the Center for Student Involvement in SC-218"}]},
                  {"task": "Get reimbursed for travel", "applies_to": "College employees",
                   "handled_by": "Controller", "term": "", "section": "Travel", "steps": [
                       {"text": "Submit the travel form.", "quote": "Submit the travel form"}]}]}
    unlisted = {**base, "id": "page-2", "source_record_key": "www.ramapo.edu/clubs/old/",
                "url": "https://www.ramapo.edu/clubs/old/",
                "sections": [{"heading": "Start a club", "text": "Start a club with ten members."}]}
    prop, _ = procedures_property([listed, unlisted], NOW, "start a new club")
    headings = [v["value"]["heading"] for v in prop["values"]]
    # The entry names the task; the page's protest section is not offered beside it, and a page
    # with no entries still offers its sections.
    assert headings[0] == "Start a new student club"
    assert "Expressive Activity" not in headings and "Start a club" in headings
    first = prop["values"][0]["value"]
    assert first["text"] == "1. Ask the Center for Student Involvement in SC-218."
    # An entry carries its section's own words matching the question, for what its steps leave out.
    table = "First violation: $200 fine. Second violation: $300 fine."
    sanctions = {**listed, "sections": [{"heading": "Alcohol", "text": table}],
                 "howto_entries": [{"task": "Know the alcohol sanctions", "section": "Alcohol",
                                    "steps": [{"text": "A first violation carries a $200 fine.",
                                               "quote": "First violation: $200 fine"}]}]}
    alcohol, _ = procedures_property([sanctions], NOW, "second alcohol violation")
    said = alcohol["values"][0]["value"]
    assert "Second violation: $300 fine." in said["text"] + said.get("page_words", "")
    # The asked rule can be in another section of the same page.
    items = {**listed, "sections": [
        {"heading": "Allowed items", "text": "Fish tanks up to 10 gallons are allowed."},
        {"heading": "Prohibited items", "text": "Microwaves other than the College's are banned."}],
             "howto_entries": [{"task": "Check which items are allowed", "section": "Allowed items",
                                "steps": [{"text": "Fish tanks up to 10 gallons are allowed.",
                                           "quote": "Fish tanks up to 10 gallons are allowed"}]}]}
    # A long list without sentence ends is read in pieces: the piece naming the item is said.
    banned = "Items not allowed: " + " ".join(f"item number {n}" for n in range(200)) + (
        " Bed risers and lofted furniture of any kind "
        + " ".join(f"thing {n}" for n in range(200)))
    risers = {**listed, "sections": [{"heading": "Prohibited", "text": banned}],
              "howto_entries": [{"task": "Check which items you may not bring",
                                 "section": "Prohibited",
                                 "steps": [{"text": "Some items are not allowed.",
                                            "quote": "Items not allowed"}]}]}
    lofted, _ = procedures_property([risers], NOW, "bring bed risers lofted bed")
    assert "Bed risers and lofted furniture" in lofted["values"][0]["value"]["page_words"]
    # A picker reads the part that matches, not the opening definitions.
    policy = {**listed, "howto_entries": [], "sections": [{"heading": "Procedure", "text": (
        "I. Definitions. " + "Words defined here at length. " * 20
        + "Protests and peaceful demonstrations must be registered with the Center.")}]}
    protest, _ = procedures_property([policy], NOW, "organize a peaceful protest")
    assert "Protests and peaceful demonstrations" in protest["values"][0]["value"]["match"]
    microwave, _ = procedures_property([items], NOW, "bring a microwave items allowed")
    assert "Microwaves other than the College's are banned." in (
        microwave["values"][0]["value"]["page_words"])
    travel, _ = procedures_property([listed], NOW, "get reimbursed for travel")
    assert travel["values"][0]["value"]["applies_to"] == "College employees"
    assert travel["values"][0]["value"]["handled_by"] == "Controller"


def advising_and_tutoring() -> MemoryEntityFacts:
    def office(key: str, name: str, url: str, sections: list[dict[str, str]]) -> tuple[
            dict[str, Any], dict[str, Any], dict[str, Any]]:
        page = {"id": f"page-{key}", "source_key": "office-pages", "source_record_key": url,
                "site_folder": key, "url": f"https://{url}", "sections": sections,
                "collected_at": NOW, "freshness_sla_hours": 168, "content_hash": f"h-{key}"}
        entity = {"id": key, "name": name, "kind": "office", "aliases": [], "links": [
            {"collection": "contacts", "source_key": "directory", "source_record_keys": [key]},
            {"collection": "office_sections", "source_key": "office-pages",
             "source_record_keys": [url]}]}
        contact = {"id": f"source-{key}", "source_key": "directory", "source_record_key": key,
                   "name": name, "email": f"{key}@example.edu",
                   "canonical_url": f"https://example.edu/{key}", "collected_at": NOW,
                   "freshness_sla_hours": 24}
        return entity, contact, page
    rows = [office("advising", "Academic Advising", "www.ramapo.edu/advising/", [
                {"heading": "Meet your advisor", "text": "Book an advising appointment."},
                {"heading": "Peer mentors", "text": "Become a peer mentor for first-years."}]),
            office("tutoring", "Center for Reading and Writing", "www.ramapo.edu/crw/tutor/", [
                {"heading": "Become a peer tutor",
                 "text": "Apply to become a peer tutor with the online form by March 1."}]),
            office("library", "Library", "www.ramapo.edu/library/", [
                {"heading": "Borrowing", "text": "Borrow books with your ID."}])]
    return MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[entity for entity, _, _ in rows], contacts=[contact for _, contact, _ in rows],
        records={"office_sections": [page for _, _, page in rows]}, now=lambda: NOW)


def test_a_how_to_on_another_offices_pages_is_offered_and_said_as_that_office() -> None:
    service = advising_and_tutoring()
    # Only the offices whose pages speak to it, never the one already asked.
    found = service.offices_by_procedure("become a peer tutor", "release-1",
                                         identity_hash="identities-1", exclude=["advising"])
    assert [office["name"] for office in found] == ["Center for Reading and Writing"]
    # The asked office's own pages match best; another office is weighed against the others only.
    found = service.offices_by_procedure(
        "book an advising appointment to meet your advisor about a tutor", "release-1",
        identity_hash="identities-1", exclude=["advising"])
    assert [office["name"] for office in found] == ["Center for Reading and Writing"]
    ask = completion("graph_lookup", {"requests": [
        {"query": "Academic Advising", "fields": ["procedures"], "about": "become a peer tutor"}]})
    pick = completion("pick_sections", {"picks": [{"lookup": 0, "sections": []},
                                                  {"lookup": 1, "sections": [0]}]})
    gateway = ScriptedGateway(ask, pick, finish())
    result = answer(gateway, service=service,
                    messages=[{"role": "user", "content": "how do i become a peer tutor"}])
    assert "March 1" in result.body["answer"]
    assert result.trace is not None
    (other,) = [e for e in result.trace["lookups"] if e.get("foundBy") == "other_office_pages"]
    assert other["office"] == "Center for Reading and Writing"
    # The picker was told which lookup the student did not ask for.
    offered = json.loads(gateway.inputs[1][1]["content"])["lookups"]
    assert "other_office" not in offered[0] and offered[1]["other_office"] is True
    # Not picked, another office is never said.
    none = completion("pick_sections", {"picks": [{"lookup": 0, "sections": [1]},
                                                  {"lookup": 1, "sections": []}]})
    kept = answer(ScriptedGateway(ask, none, finish()), service=service,
                  messages=[{"role": "user", "content": "how do i become a peer tutor"}])
    assert "March 1" not in kept.body["answer"]


def test_the_pages_are_searched_with_the_pages_own_terms_beside_the_students() -> None:
    service = advising_and_tutoring()
    # The student's words share nothing with the page; the page's own term finds it.
    ask = completion("graph_lookup", {"requests": [
        {"query": "Academic Advising", "fields": ["procedures"],
         "about": "help other kids with their essays", "page_terms": ["peer tutor"]}]})
    pick = completion("pick_sections", {"picks": [{"lookup": 0, "sections": []},
                                                  {"lookup": 1, "sections": [0]}]})
    gateway = ScriptedGateway(ask, pick, finish())
    result = answer(gateway, service=service,
                    messages=[{"role": "user", "content": "can i help other kids with essays"}])
    assert "March 1" in result.body["answer"]
    # The picker is told today's date, so it can tell this semester's section from the next.
    assert json.loads(gateway.inputs[1][1]["content"])["today"].endswith(f", {NOW.year}")


def test_a_picked_long_section_is_said_without_the_pickers_snippet() -> None:
    long_text = "Definitions: " + "Words defined at length. " * 30 + (
        "Protests must be registered with the Center for Student Involvement.")
    page = {"id": "page-1", "source_key": "office-pages",
            "source_record_key": "www.ramapo.edu/registrar/policy/", "site_folder": "registrar",
            "url": "https://www.ramapo.edu/registrar/policy/", "collected_at": NOW,
            "freshness_sla_hours": 168, "content_hash": "h1",
            "sections": [{"heading": "Procedure", "text": long_text}]}
    service = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "registrar", "name": "Registrar", "kind": "office", "aliases": [],
                   "links": [{"collection": "contacts", "source_key": "directory",
                              "source_record_keys": ["registrar"]},
                             {"collection": "office_sections", "source_key": "office-pages",
                              "source_record_keys": [page["source_record_key"]]}]}],
        contacts=[{"id": "source-registrar", "source_key": "directory",
                   "source_record_key": "registrar", "name": "Registrar",
                   "email": "registrar@example.edu", "canonical_url": "https://example.edu/reg",
                   "collected_at": NOW, "freshness_sla_hours": 24}],
        records={"office_sections": [page]}, now=lambda: NOW)
    ask = completion("graph_lookup", {"requests": [
        {"query": "Registrar", "fields": ["procedures"], "about": "register a protest"}]})
    pick = completion("pick_sections", {"picks": [{"lookup": 0, "sections": [0]}]})
    result = answer(ScriptedGateway(ask, pick, finish()), service=service,
                    messages=[{"role": "user", "content": "how do i register a protest"}])
    # The snippet leaves the value and its assertion alike, so the reply is rendered, not failed.
    assert result.status_code == 200
    assert "https://www.ramapo.edu/registrar/policy/" in result.body["answer"]


def test_three_offices_procedures_fit_one_reply_in_the_codes_own_text() -> None:
    from rockygpt_brain.answers import PROCEDURE_TEXT_CHARS, _value_text
    long = {"heading": "Academic Dismissal Appeals", "page": "https://www.ramapo.edu/a/",
            "text": "Step. " * 400, "text_cut": True}
    said = _value_text("procedures", long)
    assert said.startswith("Academic Dismissal Appeals: Step.")
    assert said.endswith("(see https://www.ramapo.edu/a/)")
    assert len(said) < PROCEDURE_TEXT_CHARS + 120


def test_rare_words_and_word_forms_pick_the_section_that_answers() -> None:
    from rockygpt_brain.retrieval.entity_facts import procedures_property

    def page(n: int, sections: list[tuple[str, str]]) -> dict[str, Any]:
        return {"id": f"p{n}", "source_key": "office-pages", "source_record_key": f"k{n}",
                "url": f"https://www.ramapo.edu/registrar/{n}/", "collected_at": NOW,
                "freshness_sla_hours": 168, "content_hash": f"h{n}",
                "sections": [{"heading": h, "text": t} for h, t in sections]}
    rows = [
        page(1, [("Declare a major",
                  "Use the add/drop form to add or drop a major or minor class."),
                 ("Add/Drop for classes",
                  "Add or drop a class during add/drop on Web Self-Service.")]),
        page(2, [("Withdrawing from a Course",
                  "After the add/drop period a withdrawal gets a W grade.")]),
        page(3, [("Class schedules", "The class schedule lists every class.")]),
    ]
    prop, _ = procedures_property(rows, NOW, "withdraw from a course, drop a class after add/drop")
    headings = [v["value"]["heading"] for v in prop["values"]]
    assert headings[0] == "Withdrawing from a Course"
    # A section far below the best is left out rather than padding the answer.
    assert "Class schedules" not in headings


def test_a_failed_pick_keeps_the_best_ranked_sections() -> None:
    ask = completion("graph_lookup", {"requests": [
        {"query": "Registrar", "fields": ["procedures"], "about": "request a transcript"}]})
    odd = completion("write_answer", {"text": "not a pick"})  # Not the pick tool: no choice made.
    result = answer(ScriptedGateway(ask, odd, finish()), service=registrar_with_pages(),
                    messages=[{"role": "user", "content": "how do i get my transcript"}])
    assert "Parchment" in result.body["answer"]
    assert result.trace is not None and result.trace["procedurePicks"] == "ranked"
