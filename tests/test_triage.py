"""Read the message first (triage.py): what kind of message it is, before anything is searched,
with code writing every reply that is not a campus answer."""

import asyncio
from typing import Any

import pytest

from rockygpt_brain import triage
from rockygpt_brain.boundary import SAFETY_TEXTS, names_a_danger
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine, ChatResult
from rockygpt_brain.provider import Completion, GatewayError, TurnBudget
from rockygpt_brain.retrieval import MemoryEntityFacts
from rockygpt_brain.turn import intake
from test_academic_calendar import MONDAY, URL, calendar
from test_engine import LOOKUP, NOW, MeteredGateway, ScriptedGateway, completion, finish


def read(**fields: Any) -> Completion:
    return completion("read_message", {"social": "none", "danger": "none", "needs": [],
                                       "limit": "none", "question": "", **fields})


def office(key: str, name: str, email: str, phone: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return ({"id": key, "name": name, "kind": "office", "aliases": [],
             "links": [{"collection": "contacts", "source_key": "directory",
                        "source_record_keys": [key]}]},
            {"id": f"source-{key}", "source_key": "directory", "source_record_key": key,
             "name": name, "email": email, "phone": phone,
             "canonical_url": f"https://example.edu/{key}", "collected_at": NOW,
             "freshness_sla_hours": 24})


def campus() -> MemoryEntityFacts:
    rows = [office("registrar", "Registrar", "published@example.edu", "+12015550100"),
            office("counseling", "Counseling Center", "counseling@example.edu", "+12015550101"),
            office("csi", "Center for Student Involvement", "csi@example.edu", "+12015550102")]
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=[entity for entity, _ in rows],
                             contacts=[contact for _, contact in rows], now=lambda: NOW)


def ask(gateway: Any, message: str, *, service: MemoryEntityFacts | None = None,
        history: tuple[dict[str, str], ...] = (), floor: str | None = None,
        now: Any = NOW) -> ChatResult:
    request = ChatRequest.model_validate(
        {"messages": [*history, {"role": "user", "content": message}]})
    engine = ChatEngine(gateway, service or campus(), output="written", read_first=True)
    return asyncio.run(engine.answer(intake(request, now=now), request, floor_situation=floor))


def latest_asked(model_input: list[dict[str, Any]]) -> str:
    """The latest message the answering model was shown."""
    import json
    for item in model_input:
        if item.get("role") == "user" and "latest_message" in str(item.get("content")):
            return str(json.loads(item["content"])["latest_message"])
    raise AssertionError("no latest message")


def test_a_read_is_checked_field_by_field_and_its_question_adds_no_number() -> None:
    fields = {"social": "greeting", "danger": "none", "needs": ["lonely", "made_up"],
              "limit": "none", "question": "Where  is the library?"}
    found = triage.reading_of(fields, "heyy wheres the libary")
    assert found == triage.Reading("greeting", "none", ("lonely",), "none",
                                   "Where is the library?")
    assert triage.reading_of({**fields, "social": "chat"}, "x") is None
    assert triage.reading_of({**fields, "needs": "lonely"}, "x") is None
    assert triage.reading_of("not a dict", "x") is None

    def question(asked: str, message: str) -> str:
        reading = triage.reading_of({**fields, "question": asked}, message)
        assert reading is not None
        return reading.question

    # A number the message does not hold, a question too long or with no words: the student's
    # own words are used instead.
    assert question("Is the library open until 10?", "is the libary open late") == ""
    assert question("x" * 301, "hi") == ""
    assert question("??", "??") == ""
    # Nothing to fix: the student's own words, as written.
    assert question("when is the library open", "When is the Library open?") == (
        "When is the Library open?")


@pytest.mark.parametrize(("fields", "said"), [
    ({"social": "greeting"}, triage.SOCIAL_TEXTS["greeting"]),
    ({"social": "about_bot"}, triage.SOCIAL_TEXTS["about_bot"]),
    ({"social": "nonsense"}, triage.SOCIAL_TEXTS["nonsense"]),
    ({"social": "complaint"}, triage.SOCIAL_TEXTS["complaint"]),
    ({"limit": "off_topic"}, triage.LIMIT_TEXTS["off_topic"]),
    ({"limit": "my_records"}, triage.LIMIT_TEXTS["my_records"] + triage.LIMIT_ALONE["my_records"]),
    ({"social": "greeting", "limit": "homework"}, "Hi! " + triage.LIMIT_TEXTS["homework"]),
])
def test_a_message_with_no_campus_question_gets_codes_reply_from_the_read_alone(
        fields: dict[str, str], said: str) -> None:
    gateway = MeteredGateway(read(**fields))
    result = ask(gateway, "yo whats good")
    assert result.status_code == 200 and result.body["answer"] == said
    assert result.body["citations"] == []
    assert result.trace is not None and result.trace["decidedBy"] == "read"
    # The read, and nothing searched or written: its call and spend are the turn's.
    assert (result.trace["modelCalls"], result.trace["committedNusd"]) == (1, 7)
    assert gateway.inputs[0][0]["content"] == triage.READ_PROMPT


def test_a_joke_and_the_clock_are_codes_own_words() -> None:
    joke = ask(ScriptedGateway(read(social="joke")), "tell me a joke").body["answer"]
    assert joke == f"{triage.joke('tell me a joke')} {triage.JOKE_AFTER}"
    clock = ask(ScriptedGateway(read(limit="clock")), "wat time is it").body["answer"]
    assert clock.startswith("The campus date and time is Thursday, October 01, 2026 at 08:00 AM")


def test_okay_after_an_emergency_keeps_the_911_and_988_reminder() -> None:
    history = ({"role": "user", "content": "my friend isnt breathing"},
               {"role": "assistant", "content": SAFETY_TEXTS["medical"]})
    said = ask(ScriptedGateway(read(social="okay")), "ok shes fine now", history=history)
    assert "call 911, or call or text 988" in said.body["answer"]
    plain = ask(ScriptedGateway(read(social="okay")), "ok cool")
    assert plain.body["answer"] == triage.SOCIAL_TEXTS["okay"]


def test_danger_in_any_wording_gets_the_same_reply_as_the_phrase_floor() -> None:
    message = "i just want everything to stop forever"
    assert not names_a_danger(message)  # The phrase floor does not know this wording.
    result = ask(ScriptedGateway(read(danger="self_harm")), message)
    assert result.body["answer"].startswith(SAFETY_TEXTS["self_harm"])
    assert result.body["status"] == "partial"
    assert result.trace is not None and result.trace["situation"] == "self_harm"


def test_a_question_beside_an_emergency_the_read_found_is_answered_after_it() -> None:
    # As the phrase floor does for a process asked beside an emergency (Dan 10-20).
    gateway = MeteredGateway(read(danger="medical", question="Who handles transcripts?"),
                             LOOKUP, finish())
    result = ask(gateway, "my roommate wont wake up and idk who handles transcripts")
    answer = result.body["answer"]
    assert answer.startswith(SAFETY_TEXTS["medical"])
    assert "published@example.edu" in answer and result.body["status"] == "partial"
    assert latest_asked(gateway.inputs[1]) == "Who handles transcripts?"
    assert result.trace is not None and result.trace["situation"] == "medical"


def test_self_harm_is_the_emergency_reply_alone_whatever_else_is_asked() -> None:
    gateway = MeteredGateway(read(danger="self_harm", question="Who handles transcripts?"))
    result = ask(gateway, "i dont want to be here anymore. who handles transcripts")
    assert result.body["answer"].startswith(SAFETY_TEXTS["self_harm"])
    assert "published@example.edu" not in result.body["answer"]
    assert len(gateway.inputs) == 1  # The read; nothing else waits before the reply.


def test_an_answer_that_fails_beside_an_emergency_leaves_the_emergency_reply() -> None:
    def provider_down() -> Completion:
        raise GatewayError("provider_unavailable", retryable=True)

    gateway = MeteredGateway(read(danger="danger", question="Who handles transcripts?"),
                             provider_down)
    result = ask(gateway, "some guy is following me rn also who does transcripts")
    assert result.status_code == 200
    assert result.body["answer"].startswith(SAFETY_TEXTS["danger"])
    assert result.trace is not None and result.trace["decidedBy"] == "read"


def test_a_feeling_gets_a_kind_sentence_and_the_offices_for_it_read_from_the_graph() -> None:
    result = ask(ScriptedGateway(read(needs=["lonely"])), "i have no one to sit with at lunch")
    assert result.body["answer"] == (
        f"{triage.SUPPORT_TEXTS['lonely']}\n\nThese offices can help:\n"
        "- **Counseling Center**: Phone: (201) 555-0101. Email: counseling@example.edu. [1]\n"
        "- **Center for Student Involvement**: Phone: (201) 555-0102. Email: csi@example.edu. "
        "[2]\n\nSources: [1](https://example.edu/counseling) [2](https://example.edu/csi)")
    assert [c["url"] for c in result.body["citations"]] == [
        "https://example.edu/counseling", "https://example.edu/csi"]
    # An office the release does not publish is skipped, never guessed.
    alone = ask(ScriptedGateway(read(needs=["disability"])), "i need accommodations")
    assert alone.body["answer"] == triage.SUPPORT_TEXTS["disability"]


def test_slang_reaches_codes_calendar_answer_in_the_clean_questions_words() -> None:
    gateway = MeteredGateway(read(question="When are finals?"))
    result = ask(gateway, "wen r finals", service=calendar(), now=MONDAY)
    assert result.body["answer"] == (
        "The Fall 2026 academic calendar lists [1]:\n"
        "- Wednesday, December 9, 2026: Full and Session II Courses - Final Exams (through "
        f"December 15)\n\nSources: [1]({URL})")
    assert result.trace is not None and result.trace["decidedBy"] == "code"
    assert result.trace["modelCalls"] == 1 and result.trace["read"]["question"] == (
        "When are finals?")


def test_a_message_code_already_understands_is_not_read() -> None:
    gateway = ScriptedGateway()  # Any model call fails: none is scripted.
    result = ask(gateway, "When are finals?", service=calendar(), now=MONDAY)
    assert result.body["answer"].startswith("The Fall 2026 academic calendar lists")
    assert gateway.inputs == [] and result.trace is not None and "read" not in result.trace


class Failing(ScriptedGateway):
    """The read fails; the rest of the turn is scripted."""

    async def complete(self, *, input: list[dict[str, Any]], tools: list[dict[str, Any]],
                       budget: TurnBudget) -> Completion:
        if tools and tools[0].get("name") == "read_message":
            raise GatewayError("provider_unavailable", retryable=True)
        return await super().complete(input=input, tools=tools, budget=budget)


@pytest.mark.parametrize("gateway", [
    ScriptedGateway(completion("read_message", {"social": "bogus"}), LOOKUP, finish()),
    Failing(LOOKUP, finish()),
])
def test_a_read_that_fails_leaves_the_turn_as_it_was(gateway: ScriptedGateway) -> None:
    result = ask(gateway, "hey so who handles transcripts and stuff")
    assert result.status_code == 200 and "published@example.edu" in result.body["answer"]
    assert result.trace is not None and result.trace["read"]["failed"] in (
        "malformed", "provider_unavailable")
    assert latest_asked(gateway.inputs[-1]) == "hey so who handles transcripts and stuff"


def test_a_limit_with_a_campus_side_says_what_it_cant_do_then_answers_that() -> None:
    # The clean question reaches code's own office answer, which slang alone did not.
    gateway = MeteredGateway(read(limit="do_it_for_me", question="What is the Registrar email?"))
    result = ask(gateway, "can u email the registrar 4 me")
    assert result.body["answer"] == (
        f"{triage.LIMIT_TEXTS['do_it_for_me']}\n\nRegistrar email: published@example.edu. [1]"
        "\n\nSources: [1](https://example.edu/registrar)")
    assert result.trace is not None and result.trace["modelCalls"] == 1


def test_the_model_answers_the_clean_question_not_the_slang() -> None:
    gateway = MeteredGateway(
        read(limit="do_it_for_me", question="Who handles transcripts?"), LOOKUP, finish())
    result = ask(gateway, "can u order my transcript 4 me")
    answer = result.body["answer"]
    assert answer.startswith(triage.LIMIT_TEXTS["do_it_for_me"] + "\n\n")
    assert "published@example.edu" in answer
    assert latest_asked(gateway.inputs[1]) == "Who handles transcripts?"
    assert result.trace is not None and result.trace["modelCalls"] == 3


def test_a_follow_up_keeps_the_students_own_words() -> None:
    history = ({"role": "user", "content": "whats the registrar email"},
               {"role": "assistant", "content": "Registrar email: published@example.edu. [1]"})
    gateway = ScriptedGateway(read(question="And their phone number?"), LOOKUP, finish())
    ask(gateway, "n there # ?", history=history)
    assert latest_asked(gateway.inputs[1]) == "n there # ?"


def test_a_feeling_beside_a_question_gets_the_kind_sentence_the_answer_and_the_offices() -> None:
    gateway = ScriptedGateway(
        read(needs=["stress_or_sadness"], question="What is the Registrar email?"), LOOKUP,
        finish())
    answer = ask(gateway, "im so stressed, whats the registrar email").body["answer"]
    kind = triage.SUPPORT_TEXTS["stress_or_sadness"]
    assert answer.startswith(f"{kind}\n\nRegistrar email: published@example.edu. [1]\n\n")
    assert answer.endswith(
        "These offices can help:\n**Counseling Center**: Phone: (201) 555-0101. Email: "
        "counseling@example.edu. [2]\n\nSources: [1](https://example.edu/registrar) "
        "[2](https://example.edu/counseling)")


def test_the_question_beside_a_floor_emergency_is_answered_like_any_other() -> None:
    gateway = ScriptedGateway(LOOKUP, finish())  # No read: the floor already read it.
    result = ask(gateway, "someone broke into my car, not 911 obviously, who do i call",
                 floor="danger")
    answer = result.body["answer"]
    assert answer.startswith(SAFETY_TEXTS["danger"] + "\n\n")
    assert "Registrar email: published@example.edu." in answer
    assert result.body["status"] == "partial"
    assert result.trace is not None and "read" not in result.trace
