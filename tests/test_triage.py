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
            office("csi", "Center for Student Involvement", "csi@example.edu", "+12015550102"),
            office("crw", "Center for Reading and Writing", "crw@example.edu", "+12015550103")]
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=[entity for entity, _ in rows],
                             contacts=[contact for _, contact in rows], now=lambda: NOW)


def ask(gateway: Any, message: str, *, service: MemoryEntityFacts | None = None,
        history: tuple[dict[str, str], ...] = (), floor: str | None = None,
        now: Any = NOW, reads: int = 1) -> ChatResult:
    """One turn. The message is read once, as each test scripts one read, unless `reads` says
    how many reads are scripted (the vote of three is tested on its own)."""
    request = ChatRequest.model_validate(
        {"messages": [*history, {"role": "user", "content": message}]})
    engine = ChatEngine(gateway, service or campus(), output="written", read_first=True,
                        reads=reads)
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
    ({"social": "praise"}, triage.SOCIAL_TEXTS["praise"]),
    ({"limit": "off_topic"}, triage.LIMIT_TEXTS["off_topic"]),
    ({"limit": "my_records"}, triage.LIMIT_TEXTS["my_records"] + triage.LIMIT_ALONE["my_records"]),
    ({"social": "greeting", "limit": "off_topic"}, "Hi! " + triage.LIMIT_TEXTS["off_topic"]),
    # A reply that says no also says what RockyGPT can do (11-03 practice).
    ({"limit": "prediction"}, triage.LIMIT_TEXTS["prediction"] + triage.LIMIT_ALONE["prediction"]),
    ({"limit": "live_status"},
     triage.LIMIT_TEXTS["live_status"] + triage.LIMIT_ALONE["live_status"]),
    ({"social": "upset"}, triage.SOCIAL_TEXTS["upset"]),
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


@pytest.mark.parametrize("labels", [triage.SOCIAL, triage.DANGER, triage.NEEDS, triage.LIMITS])
def test_no_two_labels_of_a_field_start_alike(labels: tuple[str, ...]) -> None:
    # Labels told apart only by their ends let one slip say the opposite: an insult was once read
    # as "compliment", not "complaint".
    starts = [label[:4] for label in labels]
    assert len(set(starts)) == len(starts), sorted(s for s in starts if starts.count(s) > 1)


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


def test_a_passing_upset_gets_a_short_word_back_and_no_offices() -> None:
    gateway = MeteredGateway(read(social="upset", question="What is the Registrar email?"))
    result = ask(gateway, "ugh my form got rejected again, whats the registrar email")
    assert result.body["answer"] == (
        "Sorry to hear that!\n\nRegistrar email: published@example.edu. [1]\n\nSources: "
        "[1](https://example.edu/registrar)")


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
        "This office can help:\n**Counseling Center**: Phone: (201) 555-0101. Email: "
        "counseling@example.edu. [2]\n\nSources: [1](https://example.edu/registrar) "
        "[2](https://example.edu/counseling)")


def test_the_question_beside_a_floor_emergency_is_answered_like_any_other() -> None:
    # The floor's message is read like any other (11-03 check set), and its clean question is
    # answered after the emergency text.
    gateway = MeteredGateway(read(question="Who do I call about my car?"), LOOKUP, finish())
    result = ask(gateway, "someone broke into my car, not 911 obviously, who do i call",
                 floor="danger")
    answer = result.body["answer"]
    assert answer.startswith(SAFETY_TEXTS["danger"] + "\n\n")
    assert "Registrar email: published@example.edu." in answer
    assert result.body["status"] == "partial"
    assert latest_asked(gateway.inputs[1]) == "Who do I call about my car?"
    assert result.trace is not None and result.trace["read"]["question"] == (
        "Who do I call about my car?")


@pytest.mark.parametrize(("limit", "asked"), sorted(triage.LIMIT_QUESTIONS.items()))
def test_homework_and_writing_always_ask_their_campus_side(limit: str, asked: str) -> None:
    # 11-03 practice: "solve this: integral of x^2 dx" names no subject, and the read left the
    # question empty, so the reply said no and offered nothing. Tutoring is the campus side.
    gateway = MeteredGateway(read(limit=limit), LOOKUP, finish())
    result = ask(gateway, "solve this for me pls")
    assert result.body["answer"].startswith(triage.LIMIT_TEXTS[limit] + "\n\n")
    assert latest_asked(gateway.inputs[1]) == asked
    assert result.trace is not None and result.trace["read"]["corrected"]["why"] == (
        "limit_question")
    # A follow-up asks it too: it has no "it" or "that" to keep in the student's words.
    history = ({"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hi!"})
    again = MeteredGateway(read(limit=limit), LOOKUP, finish())
    ask(again, "ok now do my essay", history=history)
    assert latest_asked(again.inputs[1]) == asked


def test_a_writing_request_names_the_writing_center_after_the_answer() -> None:
    # 11-03 practice: "write my personal statement for the nursing program" was sent only to the
    # fellowships office's personal-statement advice, which the student's words found. Help
    # writing is the writing center's job whatever the page search finds, so code names it.
    gateway = ScriptedGateway(read(limit="writing", question=(
        "Where can I get help writing my personal statement for the nursing program?")),
        LOOKUP, finish())
    answer = ask(gateway, "write my personal statement for the nursing program").body["answer"]
    assert answer.startswith(triage.LIMIT_TEXTS["writing"] + "\n\n")
    assert answer.endswith(
        "This office can help:\n**Center for Reading and Writing**: Phone: (201) 555-0103. "
        "Email: crw@example.edu. [2]\n\nSources: [1](https://example.edu/registrar) "
        "[2](https://example.edu/crw)")
    # A reply that already names it doesn't list it again.
    named = ScriptedGateway(
        read(limit="writing", question="What is the Center for Reading and Writing's email?"),
        completion("graph_lookup", {"requests": [
            {"query": "Center for Reading and Writing", "fields": ["email"]}]}), finish())
    answer = ask(named, "write my essay").body["answer"]
    assert "crw@example.edu" in answer and "can help:" not in answer


def test_offices_for_a_problem_come_first_then_the_one_for_the_limit_each_once() -> None:
    assert triage.support_offices(["lonely"], "writing") == [
        "Counseling Center", "Center for Student Involvement", "Center for Reading and Writing"]
    assert triage.support_offices([], "homework") == []
    assert triage.support_offices(["stress_or_sadness", "lonely"]) == [
        "Counseling Center", "Center for Student Involvement"]
    assert triage.support_lead(1) == "This office can help:"
    assert triage.support_lead(2) == "These offices can help:"


def test_a_message_that_names_a_campus_office_is_never_nonsense() -> None:
    # 11-03 practice: "registrar # ?" was read as nonsense. Code knows the listed names, so a
    # message that names one goes on as a question, in the student's own words.
    gateway = MeteredGateway(read(social="nonsense"), LOOKUP, finish())
    result = ask(gateway, "counseling center???")
    assert latest_asked(gateway.inputs[1]) == "counseling center???"
    assert result.trace is not None and result.trace["read"]["corrected"]["why"] == (
        "names_a_place")
    assert result.body["answer"] != triage.SOCIAL_TEXTS["nonsense"]
    # Mashed keys name nothing: still nonsense, from the read alone.
    mashed = ask(MeteredGateway(read(social="nonsense")), "jdjdjsjskskaka")
    assert mashed.body["answer"] == triage.SOCIAL_TEXTS["nonsense"]
    assert mashed.trace is not None and "corrected" not in mashed.trace["read"]


def test_a_question_that_only_asks_the_date_is_answered_by_the_clock_line_alone() -> None:
    # 11-03 practice: the date was said twice, once as the clock line and once as the answer to
    # the read's question "What is today's date?".
    gateway = MeteredGateway(read(limit="clock", question="What's the date today?"))
    result = ask(gateway, "date pls")
    assert result.body["answer"].startswith("The campus date and time is ")
    assert result.body["answer"].count("2026") == 1
    assert result.trace is not None and result.trace["modelCalls"] == 1
    assert result.trace["read"]["corrected"]["why"] == "clock_only"
    # The date with a campus question beside it still answers the question.
    both = MeteredGateway(read(limit="clock", question="What is the Registrar email?"))
    answer = ask(both, "date pls and registrar email").body["answer"]
    assert answer.startswith("The campus date and time is ")
    assert "published@example.edu" in answer
    assert [triage.asks_only_the_clock(q) for q in (
        "What time is it right now?", "what day is it", "What day are finals?",
        "What time does the library close?", "What is the date of the career fair?")] == [
        True, True, False, False, False]


def test_todays_published_hours_menus_and_events_are_no_live_update() -> None:
    # 11-03 practice: "today" and "tonight" were read as live updates, and a published menu and
    # tonight's events came after "I can't see live updates".
    gateway = MeteredGateway(read(limit="live_status",
                                  question="What are the Registrar's hours today?"))
    result = ask(gateway, "registrar hrs 2day")
    assert not result.body["answer"].startswith(triage.LIMIT_TEXTS["live_status"])
    assert result.trace is not None and result.trace["read"]["corrected"]["why"] == (
        "published_lookup")
    # How busy a place is right now is live, whatever place is named.
    busy = MeteredGateway(read(limit="live_status", question="Is the Registrar busy today?"),
                          LOOKUP, finish())
    live = ask(busy, "is the registrar busy rn")
    assert live.body["answer"].startswith(triage.LIMIT_TEXTS["live_status"])
    assert live.trace is not None and "corrected" not in live.trace["read"]


@pytest.mark.parametrize(("limit", "message"), [
    ("off_topic", "any sports games coming up"),
    ("prediction", "is there a semi formal coming up"),
])
def test_what_is_coming_up_on_the_calendar_is_never_off_topic_or_a_prediction(
        limit: str, message: str) -> None:
    # 11-03 practice: "any sports games coming up" got "I only know about Ramapo College", and
    # "is there a semi formal coming up" got "I can't predict that" before its answer.
    gateway = MeteredGateway(read(limit=limit), LOOKUP, finish())
    result = ask(gateway, message)
    assert result.trace is not None and result.trace["read"]["corrected"]["why"] == (
        "calendar_lookup")
    assert not result.body["answer"].startswith(triage.LIMIT_TEXTS.get(limit, "I only know"))
    assert not result.body["answer"].startswith("I only know about Ramapo College")
    # A game's result is not on the calendar: the read's word stands.
    score = ask(MeteredGateway(read(limit="off_topic")), "who won the game last night")
    assert score.body["answer"].startswith("I only know about Ramapo College")
    assert score.trace is not None and "corrected" not in score.trace["read"]
    # Nor is the weather, though it names a time the calendar covers (11-03 practice: corrected,
    # "whats the weather gonna be like this weekend" listed the weekend's events).
    weather = ask(MeteredGateway(read(limit="prediction")),
                  "whats the weather gonna be like this weekend")
    assert weather.body["answer"].startswith(triage.LIMIT_TEXTS["prediction"])
    assert weather.trace is not None and "corrected" not in weather.trace["read"]


def test_a_hello_before_a_question_is_said_once() -> None:
    # 11-03 practice: "Hi!" came first, then the whole greeting again after the answer.
    gateway = MeteredGateway(read(social="greeting", question="Who handles transcripts?"),
                             LOOKUP, finish("greeting"))
    answer = ask(gateway, "hello! who handles transcripts").body["answer"]
    assert answer.startswith("Hi!\n\n") and "published@example.edu" in answer
    assert triage.SOCIAL_TEXTS["greeting"] not in answer


def test_a_threat_the_floor_found_is_also_read_and_answered_after_the_danger_text() -> None:
    # "My mom is going to kill me" over a quiz: the floor's danger text first, then what the read
    # found, the kind words and offices for the feeling shared (11-03 check set: the emergency
    # reply alone, with nothing for the student's real worry).
    gateway = MeteredGateway(read(needs=["stress_or_sadness"]))
    result = ask(gateway, "failed my quiz and my mom is going to kill me", floor="danger")
    answer = result.body["answer"]
    assert answer.startswith(SAFETY_TEXTS["danger"])
    assert triage.SUPPORT_TEXTS["stress_or_sadness"] in answer
    assert "counseling@example.edu" in answer and result.body["status"] == "partial"
    assert result.trace is not None and result.trace["situation"] == "danger"


def test_a_threat_the_read_also_calls_a_danger_says_the_danger_text_once() -> None:
    gateway = MeteredGateway(read(danger="danger"))
    result = ask(gateway, "someone is going to kill me", floor="danger")
    answer = result.body["answer"]
    assert answer.startswith(SAFETY_TEXTS["danger"])
    assert answer.count(SAFETY_TEXTS["danger"]) == 1


def test_a_shared_problem_gets_one_kind_sentence_not_two_sorries() -> None:
    # 11-03 check set: "Sorry to hear that! I'm sorry you're going through that."
    result = ask(ScriptedGateway(read(social="upset", needs=["stress_or_sadness"])),
                 "so nervous about my speech tmrw")
    answer = result.body["answer"]
    assert answer.startswith(triage.SUPPORT_TEXTS["stress_or_sadness"])
    assert triage.SOCIAL_LEADS["upset"] not in answer
    # Upset about a practical problem with no need shared keeps its short word back.
    assert triage.social_lead("upset", []) == triage.SOCIAL_LEADS["upset"]
    assert triage.social_lead("greeting", ["lonely"]) == "Hi!"


def test_needs_are_said_in_one_order_whatever_order_the_read_gives_them() -> None:
    # 11-03 practice: "i think im gonna fail orgo" was read once as school trouble then stress and
    # once the other way round, so one reply led with grades and the other with stress.
    assert sorted(triage.NEED_ORDER) == sorted(triage.NEEDS)
    fields = {"social": "none", "danger": "none", "limit": "none", "question": ""}
    for needs in (["stress_or_sadness", "school_trouble"], ["school_trouble", "stress_or_sadness"]):
        reading = triage.reading_of({**fields, "needs": needs}, "i think im gonna fail orgo")
        assert reading is not None and reading.needs == ("school_trouble", "stress_or_sadness")
    result = ask(ScriptedGateway(read(needs=["stress_or_sadness", "lonely"])), "i miss home")
    assert result.body["answer"].startswith(triage.SUPPORT_TEXTS["lonely"])


@pytest.mark.parametrize(("asked", "kept"), [
    ("My suitemate keeps eating my food and won't talk to me about it.", False),
    ("I think I'm going to fail organic chemistry.", False),
    ("What can I do about a suitemate who eats my food?", True),
    ("How do I switch rooms", True),
    ("I need a tutor for calculus.", True),
    ("I really want a new roommate.", True),
    ("Tell me where Residence Life is.", True),
])
def test_beside_a_problem_shared_only_a_question_that_asks_is_a_campus_question(
        asked: str, kept: bool) -> None:
    fields = {"social": "none", "danger": "none", "needs": ["roommate_trouble"], "limit": "none"}
    reading = triage.reading_of({**fields, "question": asked}, f"ugh {asked.casefold()}")
    assert reading is not None and reading.question == (asked if kept else ""), asked
    # With no problem shared, the question is the campus question however it is put.
    plain = triage.reading_of({**fields, "needs": [], "question": "Library hours."},
                              "libary hours")
    assert plain is not None and plain.question == "Library hours."


def test_a_problem_told_back_as_the_question_gets_codes_kind_reply() -> None:
    # 11-03 practice: the suitemate problem, given back as the question, was answered with every
    # contact of three offices instead of the kind sentence and the offices for it.
    gateway = ScriptedGateway(read(needs=["lonely"],
                                   question="I have no one to sit with at lunch."))
    result = ask(gateway, "i have no one to sit with at lunch")
    assert result.body["answer"].startswith(triage.SUPPORT_TEXTS["lonely"])
    assert result.trace is not None and result.trace["decidedBy"] == "read"


def test_what_most_reads_say_is_the_reading() -> None:
    # 11-03 repeat test: one read a try made "where the halal food at" a feeling in 2 of 5
    # tries; three reads at once and a vote give the same reading every time.
    halal = triage.Reading("none", "none", (), "none", "Where can I find halal food?")
    upset = triage.Reading("upset", "none", ("food_or_housing",), "none",
                           "Where is the halal food?")
    assert triage.majority([halal, upset, halal]) == halal
    assert triage.majority([upset, halal, halal]) == halal  # Whichever came back first.
    # Labels no two reads share fall back to the first listed (none); a danger any read finds
    # is kept, as the read only ever adds an emergency.
    split = [triage.Reading("greeting", "none", (), "none", ""),
             triage.Reading("thanks", "medical", (), "clock", ""),
             triage.Reading("none", "none", (), "opinion", "")]
    assert triage.majority(split) == triage.Reading("none", "medical", (), "none", "")
    # A need counts when most reads give it; the question is asked when most reads ask one,
    # in the wording nearest the others', then the shortest.
    worded = [triage.Reading("none", "none", ("money",), "none", "How do I pay my bill?"),
              triage.Reading("none", "none", ("money",), "none", "How can I pay my bill?"),
              triage.Reading("none", "none", (), "none", "Where do I pay my tuition bill online?")]
    assert triage.majority(worded) == triage.Reading("none", "none", ("money",), "none",
                                                     "How do I pay my bill?")
    told_back = [triage.Reading("upset", "none", ("lonely",), "none", ""),
                 triage.Reading("upset", "none", ("lonely",), "none", ""),
                 triage.Reading("none", "none", (), "none", "Are there clubs?")]
    assert triage.majority(told_back) == triage.Reading("upset", "none", ("lonely",), "none", "")
    assert triage.majority([]) is None


def test_the_message_is_read_three_times_at_once_and_most_reads_decide() -> None:
    gateway = MeteredGateway(read(social="greeting"), read(social="nonsense"),
                             read(social="greeting"))
    result = ask(gateway, "heyyy", reads=3)
    assert result.body["answer"] == triage.SOCIAL_TEXTS["greeting"]
    assert result.trace is not None
    assert [r["social"] for r in result.trace["read"]["reads"]] == [
        "greeting", "nonsense", "greeting"]
    assert (result.trace["modelCalls"], result.trace["committedNusd"]) == (3, 21)
    assert len(gateway.inputs) == 3 and gateway.inputs[0] == gateway.inputs[2]


def test_a_read_that_fails_leaves_the_others_to_decide() -> None:
    def provider_down() -> Completion:
        raise GatewayError("provider_unavailable", retryable=True)

    gateway = MeteredGateway(read(social="greeting"), provider_down, read(social="greeting"))
    result = ask(gateway, "heyyy", reads=3)
    assert result.body["answer"] == triage.SOCIAL_TEXTS["greeting"]
    assert result.trace is not None
    assert result.trace["read"]["readsFailed"] == ["provider_unavailable"]


def test_a_danger_one_read_finds_is_the_emergency_reply() -> None:
    gateway = MeteredGateway(read(question="Who handles transcripts?"),
                             read(danger="self_harm", question="Who handles transcripts?"),
                             read(question="Who handles transcripts?"))
    result = ask(gateway, "i dont want to be here anymore. who handles transcripts", reads=3)
    assert result.body["answer"].startswith(SAFETY_TEXTS["self_harm"])
    assert len(gateway.inputs) == 3
