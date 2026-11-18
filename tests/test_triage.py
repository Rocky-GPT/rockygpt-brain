"""Read the message first (triage.py): what kind of message it is, before anything is searched,
with code writing every reply that is not a campus answer."""

import asyncio
from typing import Any

import pytest

from rockygpt_brain import general, triage
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


def test_a_limits_campus_side_is_asked_after_the_students_own_words() -> None:
    # 11-04 blind run: the campus side the read wrote in place of the message dropped what the
    # student said ("can't register for spring classes" beside a hold). Code's lookups still get
    # the read's words (the test before this one); anything else hears the student first.
    gateway = MeteredGateway(
        read(limit="do_it_for_me", question="Who handles transcripts?"), LOOKUP, finish())
    result = ask(gateway, "can u order my transcript 4 me")
    answer = result.body["answer"]
    assert answer.startswith(triage.LIMIT_TEXTS["do_it_for_me"] + "\n\n")
    assert "published@example.edu" in answer
    assert latest_asked(gateway.inputs[1]) == (
        "can u order my transcript 4 me. Who handles transcripts?")
    assert result.trace is not None and result.trace["modelCalls"] == 3
    assert result.trace["read"]["asked"] == "own_words_then_campus_side"


def test_a_follow_up_keeps_the_students_own_words() -> None:
    history = ({"role": "user", "content": "whats the registrar email"},
               {"role": "assistant", "content": "Registrar email: published@example.edu. [1]"})
    gateway = ScriptedGateway(read(question="And their phone number?"), LOOKUP, finish())
    ask(gateway, "n there # ?", history=history)
    # Only its safe text-speak is spelled out (triage.spelled_out).
    assert latest_asked(gateway.inputs[1]) == "and there # ?"


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


def test_a_campus_question_asking_no_choice_is_no_opinion() -> None:
    # 11-04 practice: two of three reads made "does ramapo have a nursing major and how long is
    # it" an opinion, one adding "and who helps students decide?", and the reply began "I can't
    # choose for you". The student asked no choice, so their own words are asked.
    message = "does ramapo have a nursing major and how long is it"
    gateway = MeteredGateway(read(limit="opinion", question=(
        "Does Ramapo have a nursing major, how long is it, and who helps students decide?")),
        LOOKUP, finish())
    result = ask(gateway, message)
    assert result.trace is not None and result.trace["read"]["corrected"] == {
        "why": "no_choice_asked", "social": "none", "danger": "none", "needs": [],
        "limit": "none", "question": message}
    assert not result.body["answer"].startswith(triage.LIMIT_TEXTS["opinion"])
    assert latest_asked(gateway.inputs[1]) == message
    # A choice asked stays an opinion, however it is asked.
    for asked in ("is ramapo good for nursing", "which dorm should i pick",
                  "nursing or bio major?", "is the nursing program worth it"):
        kept = ask(MeteredGateway(read(limit="opinion", question="What does Ramapo publish "
                                                                 "about it?"), LOOKUP, finish()),
                   asked)
        assert kept.body["answer"].startswith(triage.LIMIT_TEXTS["opinion"]), asked
        assert kept.trace is not None and "corrected" not in kept.trace["read"], asked
    # An opinion with no campus side is answered by code alone, as before.
    alone = ask(MeteredGateway(read(limit="opinion")), "what's ur favorite color")
    assert alone.body["answer"] == (triage.LIMIT_TEXTS["opinion"]
                                    + triage.LIMIT_ALONE["opinion"])


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


@pytest.mark.parametrize(("typed", "question", "keeps"), [
    ("wen r finals", "When are finals?", True),
    ("registrar email", "What is the Registrar's email?", True),
    ("hey whats the registar phone #", "What is the Registrar phone number?", True),
    ("hi! when does the gym open", "When does the gym open?", True),
    ("calc tutoring", "Where is calculus tutoring?", True),
    ("wen does winter brk start", "When does winter break start?", True),
    ("how many classes do i need", "How many classes do I need?", True),
    ("cant log into my email", "I cannot log into my email.", True),
    # Words the student typed are left out: their situation, or a number.
    ("im a junior in biology thinking about switching to nursing, who do i talk to",
     "Who do I talk to about switching to nursing?", False),
    ("is BIOL 204 4 credits", "Is BIOL 204 four credits?", False),
    # A word the student never typed is added, or one they typed is changed.
    ("library hours", "What are the library's hours this weekend?", False),
    ("who do i call about my car", "Who do I call about my career?", False),
    ("where do i catch the bus", "Where do I catch the business?", False),
])
def test_the_reads_question_is_used_only_when_it_keeps_every_word_typed(
        typed: str, question: str, keeps: bool) -> None:
    assert triage.keeps_words(typed, question) is keeps


def test_a_clock_questions_clock_words_may_be_left_to_the_clock_line() -> None:
    typed = "whats the date and when does the library close"
    question = "When does the library close?"
    assert not triage.keeps_words(typed, question)
    assert triage.keeps_words(typed, question, triage.CLOCK_WORDS)


def test_a_message_asking_more_than_the_reads_question_is_told_apart() -> None:
    email = "What is the Registrar email?"
    assert triage.asks_more("whats the registrar email and how do i get a transcript", email)
    assert triage.asks_more("registrar email? hours?", email)
    assert not triage.asks_more("ugh my form got rejected again, whats the registrar email", email)


def test_what_a_first_message_is_asked_in() -> None:
    asked = triage.asked
    assert asked("wen r finals", triage.Reading(question="When are finals?")) == "When are finals?"
    # The student's situation dropped: their own words.
    assert asked("im a junior in biology thinking about switching to nursing, who do i talk to",
                 triage.Reading(question="Who do I talk to about switching to nursing?")) is None
    # A limit's campus side after the student's own words.
    hold = "theres a hold on my account and i cant register for spring, how do i find out why"
    assert asked(hold, triage.Reading(limit="my_records",
                                      question="How do I find out why I have a hold?")) == (
        f"{hold}. How do I find out why I have a hold?")
    # Homework's request is the part not answered, and code's emergency text answers the rest.
    tutoring = "Where can I get tutoring in chemistry?"
    assert asked("do my chem homework", triage.Reading(limit="homework", question=tutoring)) == (
        tutoring)
    assert asked("my roommate wont wake up and idk who handles transcripts",
                 triage.Reading(danger="medical", question="Who handles transcripts?")) == (
        "Who handles transcripts?")
    assert asked("thanks!", triage.Reading(social="thanks")) is None



def test_a_rule_asked_as_if_is_no_prediction_and_no_record() -> None:
    # Fix check 11-04: "if i drop a class and go part time will i lose my financial aid" began
    # "I can't predict that.", though the aid rules answer it.
    message = "if i drop a class and go part time will i lose my financial aid"
    gateway = MeteredGateway(read(limit="prediction", question=(
        "What are the financial aid requirements for part-time students?")), LOOKUP, finish())
    result = ask(gateway, message)
    assert result.trace is not None and result.trace["read"]["corrected"]["why"] == (
        "rule_question")
    assert not result.body["answer"].startswith(triage.LIMIT_TEXTS["prediction"])
    assert latest_asked(gateway.inputs[1]) == message
    # A guess no rule decides stays one.
    kept = ask(MeteredGateway(read(limit="prediction", question="What are the admission "
                                                                "requirements?"),
                              LOOKUP, finish()), "if i apply with a 3.2 will i get in")
    assert kept.body["answer"].startswith(triage.LIMIT_TEXTS["prediction"])
    assert kept.trace is not None and "corrected" not in kept.trace["read"]
    # A record read on a question naming nothing of the student's is a misread.
    course = ask(MeteredGateway(read(limit="my_records", question="Who teaches Introduction to "
                                                                  "Psychology?"),
                                LOOKUP, finish()), "who teaches intro to psych")
    assert course.trace is not None and course.trace["read"]["corrected"]["why"] == (
        "no_own_record")
    assert not course.body["answer"].startswith(triage.LIMIT_TEXTS["my_records"])
    own = ask(MeteredGateway(read(limit="my_records", question="How do I check my GPA?"),
                             LOOKUP, finish()), "whats my gpa")
    assert own.body["answer"].startswith(triage.LIMIT_TEXTS["my_records"])


@pytest.mark.parametrize(("need", "message", "dropped"), [
    # Fix check 11-04: switching majors was read as thinking of leaving ("That's a big decision").
    ("thinking_of_leaving", "im thinking about switching to psych. who is the convener",
     "no_leaving_words"),
    ("thinking_of_leaving", "im thinking about transferring out, who do i talk to", None),
    ("thinking_of_leaving", "i want to take a semester off", None),
    # A hypothetical shares no trouble ("one bad grade doesn't decide everything" was said).
    ("school_trouble", "what happens if i take an incomplete and don't finish the work in time",
     "hypothetical"),
    ("school_trouble", "i failed orgo, what happens if i retake it", None),
    ("school_trouble", "im failing calc what do i do", None),
])
def test_a_need_the_message_does_not_bear_out_is_dropped(need: str, message: str,
                                                         dropped: str | None) -> None:
    needs, why = triage.needs_checked((need,), message)
    assert needs == (() if dropped else (need,))
    assert why == ({need: dropped} if dropped else {})


def test_a_dropped_need_says_no_kind_sentence_and_names_no_office() -> None:
    message = "im thinking about switching to psych. who is the psychology convener"
    gateway = MeteredGateway(read(needs=["thinking_of_leaving"], question=message),
                             LOOKUP, finish())
    result = ask(gateway, message)
    assert result.trace is not None and result.trace["read"]["needsDropped"] == {
        "thinking_of_leaving": "no_leaving_words"}
    assert triage.SUPPORT_TEXTS["thinking_of_leaving"] not in result.body["answer"]


def test_medical_advice_is_declined_only_when_it_is_asked() -> None:
    # Fix check 11-04: "i broke my wrist and cant write for a few weeks. can OSS help me with
    # notes and exams" began "I can't give medical advice".
    hurt = "i broke my wrist and cant write for a few weeks. can OSS help me with notes and exams"
    assert triage.support_text("health", hurt, "Can OSS help with notes and exams?") == (
        triage.SUPPORT_TEXTS["health"])
    assert triage.support_text("health", "can i take advil with antibiotics, and where is "
                                         "health services", "Where is Health Services?") == (
        triage.MEDICAL_ADVICE_TEXT)
    # No campus question: the advice line is the reply, as before.
    assert triage.support_text("health", "which medicine should i take for a cold", "") == (
        triage.MEDICAL_ADVICE_TEXT)
    assert triage.support_text("lonely", hurt, "x") == triage.SUPPORT_TEXTS["lonely"]
    answer = ask(MeteredGateway(read(needs=["health"], question="Can OSS help with notes and "
                                                                "exams?"), LOOKUP, finish()),
                 hurt).body["answer"]
    assert answer.startswith(triage.SUPPORT_TEXTS["health"])
    assert triage.MEDICAL_ADVICE_TEXT not in answer
    alone = ask(MeteredGateway(read(needs=["health"])), "which medicine should i take for a cold")
    assert alone.body["answer"].startswith(triage.MEDICAL_ADVICE_TEXT)


# Audit plan item 8 (11-17): the read's topic picks the answer path, in place of the word rules.

def test_the_read_says_what_the_question_asks_for() -> None:
    arguments = {"social": "none", "danger": "none", "needs": [], "limit": "none",
                 "question": "When is the career fair?", "topic": "event"}
    def topic(given: dict[str, Any]) -> str | None:
        reading = triage.reading_of(given, "when is the career fair")
        assert reading is not None
        return reading.topic
    assert topic(arguments) == "event"
    assert triage.reading_of({**arguments, "topic": "weather"}, "x") is None
    assert topic({k: v for k, v in arguments.items() if k != "topic"}) is None
    assert "topic" in triage.READ_TOOL["parameters"]["required"]
    # Two topics never start with the same four letters (the read writes a label in pieces).
    assert len({t[:4] for t in triage.TOPICS}) == len(triage.TOPICS)


def test_the_topic_most_reads_give_is_the_reading_and_a_tie_is_a_page_answer() -> None:
    def most(*topics: str | None) -> str | None:
        reading = triage.majority([triage.Reading(question="Q?", topic=t) for t in topics])
        assert reading is not None
        return reading.topic
    assert most("event", "club", "event") == "event"
    assert most("office", "person", "how_to") == "how_to"
    assert most(None, None, "office") is None


def test_the_topic_picks_the_path_and_the_word_rules_only_without_a_read() -> None:
    from rockygpt_brain.engine import picked_path
    # "How do I book a room for a club event?" names an event and a club, which the word rules
    # read as the calendar's events; the read says it asks how to do something.
    message = "How do I book a room for a club event?"
    assert picked_path(None, message).calendar
    assert picked_path("how_to", message) == picked_path("how_to", "")
    assert not picked_path("how_to", message).calendar and picked_path("how_to", message).pages
    assert picked_path("event", "what is on tonight").calendar
    assert picked_path("club", "is there a chess thing").calendar
    assert picked_path("person", "who runs EOF").people
    assert picked_path("team", "is there a hockey team").calendar
    for lookup in ("office", "dining", "shuttle"):
        assert picked_path(lookup, message) == picked_path(lookup, "")
        assert not picked_path(lookup, message).pages
    for page in ("how_to", "dates", "team", "program", "person", "event", "club"):
        assert picked_path(page, "anything").pages


def test_the_reads_topic_is_traced_and_a_lookup_topic_keeps_the_lookup() -> None:
    gateway = ScriptedGateway(read(question="What is the Registrar email?", topic="office"),
                              LOOKUP, finish())
    result = ask(gateway, "ugh my form got rejected again, whats the registrar email")
    assert "published@example.edu" in result.body["answer"]
    assert result.trace is not None
    assert result.trace["path"] == {"topic": "office", "by": "read"}
    assert result.trace["read"]["topic"] == "office"


def test_safe_text_speak_is_spelled_out_for_code_word_matching() -> None:
    # 11-18 blind run: "n" kept a two-part events question as one topic, so the next play was
    # missed. Only text-speak with one meaning is spelled out; real words and contractions stay.
    assert triage.spelled_out("when's the next theater show n do students get tix") == (
        "when's the next theater show and do students get tix")
    assert triage.spelled_out("wen r finals? u kno") == "when are finals? you kno"
    assert triage.spelled_out("is the pool open sat n do i need my id") == (
        "is the pool open sat and do i need my id")
    assert triage.spelled_out("n/a, u.s. history, r&b") == "n/a, u.s. history, r&b"


def test_more_text_speak_keeps_the_reads_clean_question() -> None:
    # 11-18 check: 9 of 20 fresh texting questions lost the read's clean words over these.
    assert triage.keeps_words("is the pool open sat n do i need my id",
                              "Is the pool open Saturday, and do I need my ID?")
    assert triage.keeps_words("who do i talk 2 abt a hold on my acct n where r they",
                              "Who do I talk to about a hold on my account, and where are they?")
    assert triage.keeps_words("hw do i get a parking permit n how much is it",
                              "How do I get a parking permit, and how much is it?")


class ByToolGateway(MeteredGateway):
    """Answers each call from the steps scripted for its tool, whatever order the calls come in
    (the general answer runs beside the campus answer)."""

    def __init__(self, **steps: list[Completion]) -> None:
        super().__init__()
        self.by_tool = {name: iter(found) for name, found in steps.items()}
        self.tools: list[str] = []

    async def complete(self, *, input: list[dict[str, Any]], tools: list[dict[str, Any]],
                       budget: TurnBudget) -> Completion:
        name = str(tools[0]["name"]) if len(tools) == 1 else "loop"
        self.tools.append(name)
        budget.calls += 1
        budget.committed_nusd += 7
        self.inputs.append(input)
        return next(self.by_tool[name])


def general_said(text: str) -> Completion:
    return completion("write_general", {"text": text})


def test_a_need_counts_only_where_the_student_says_they_have_it() -> None:
    # 11-18 blind run: support lines sat on messages that named no such problem.
    fields = {"social": "none", "danger": "none", "needs": ["school_trouble"], "limit": "none",
              "question": "Where is the testing center?", "general": ""}
    message = "wheres the testing center n do i need an appt 4 a makeup exam"
    copied = triage.reading_of({**fields, "problem_words": "makeup exam"}, message)
    assert copied is not None and copied.needs == ("school_trouble",)
    for words in ("", "i missed my exam", "exam"):
        found = triage.reading_of({**fields, "problem_words": words}, message)
        assert found is not None and found.needs == (), words
    # A request for medical advice is health whatever is told.
    advice = triage.reading_of({**fields, "needs": ["health"], "problem_words": "",
                                "question": ""}, "can i take advil with tylenol")
    assert advice is not None and advice.needs == ("health",)
    # One word is enough when it is the whole message.
    alone = triage.reading_of({**fields, "needs": ["lonely"], "problem_words": "homesick",
                               "question": ""}, "homesick :(")
    assert alone is not None and alone.needs == ("lonely",)


def test_a_general_question_gets_a_general_answer_marked_as_general() -> None:
    # Dan 11-18: the bot answers general knowledge and everyday advice; 7 of 17 non-campus
    # questions in the 11-18 blind set were refused.
    gateway = ByToolGateway(
        read_message=[read(limit="off_topic", general="Why is the sky blue?")],
        write_general=[general_said("Sunlight scatters off air molecules, and blue light "
                                    "scatters the most.")])
    result = ask(gateway, "why is the sky blue")
    answer = result.body["answer"]
    assert answer.startswith(general.LABEL)
    assert "blue light scatters the most" in answer
    assert triage.LIMIT_TEXTS["off_topic"] not in answer
    assert result.trace is not None and result.trace["decidedBy"] == "general"
    assert result.trace["modelCalls"] == 2
    assert gateway.inputs[1][-1]["content"] == "Why is the sky blue?"


def test_a_general_answer_naming_the_college_or_a_contact_is_never_said() -> None:
    for text in ("Ramapo's gym is great for that.", "Email help@example.edu for tips.",
                 "Call (201) 555-0100.", "See https://example.org."):
        gateway = ByToolGateway(
            read_message=[read(limit="off_topic", general="How do I stay motivated?")],
            write_general=[general_said(text)])
        answer = ask(gateway, "how do i stay motivated to work out").body["answer"]
        assert answer == triage.LIMIT_TEXTS["off_topic"], text


def test_a_mixed_message_gets_the_general_part_then_the_campus_answer() -> None:
    gateway = ByToolGateway(
        read_message=[read(limit="off_topic", general="Is coffee bad for you?",
                           question="Whats the registrar email?")],
        write_general=[general_said("In moderation, coffee is fine for most adults.")],
        loop=[LOOKUP, finish()])
    answer = ask(gateway, "is coffee bad for u lol. also whats the registrar email").body[
        "answer"]
    assert answer.startswith(general.LABEL + " In moderation")
    assert triage.OFF_TOPIC_BESIDE not in answer
    assert "published@example.edu" in answer


def test_homework_and_medical_advice_get_no_general_answer() -> None:
    homework = ByToolGateway(
        read_message=[read(limit="homework", general="Solve x + 2 = 5",
                           question="Where can I get tutoring in math?")],
        loop=[LOOKUP, finish()])
    ask(homework, "solve x + 2 = 5 for me")
    assert "write_general" not in homework.tools
    medicine = ByToolGateway(read_message=[read(needs=["health"], general="Is ibuprofen safe?")])
    said = ask(medicine, "is it safe to take ibuprofen every day").body["answer"]
    assert "write_general" not in medicine.tools
    assert triage.MEDICAL_ADVICE_TEXT in said


def test_a_failed_general_answer_leaves_the_reply_as_it_was() -> None:
    gateway = ByToolGateway(
        read_message=[read(limit="off_topic", general="Who would win, a gorilla or 100 men?")],
        write_general=[completion("write_answer", {"text": "wrong tool"})])
    answer = ask(gateway, "1 gorilla vs 100 men who wins").body["answer"]
    assert answer == triage.LIMIT_TEXTS["off_topic"]


def test_a_general_part_that_is_only_the_campus_question_again_gets_no_general_answer() -> None:
    # 11-18 study: "i failed my calc midterm and idk what to do" was read with "What should I
    # do?" as both, and the general writer asked the student back what was going on.
    gateway = ByToolGateway(
        read_message=[read(general="What should I do?", question="What should I do?")],
        loop=[LOOKUP, finish()])
    ask(gateway, "i failed my midterm and idk what to do")
    assert "write_general" not in gateway.tools


def test_transferring_in_is_not_thinking_of_leaving() -> None:
    needs, dropped = triage.needs_checked(
        ("thinking_of_leaving",), "transfering in for spring and want to say hi to my advisor")
    assert needs == () and dropped == {"thinking_of_leaving": "no_leaving_words"}
    for leaving in ("im thinking about transferring to rutgers", "i want to transfer out",
                    "thinking of transferring"):
        assert triage.needs_checked(("thinking_of_leaving",), leaving)[0] == (
            "thinking_of_leaving",), leaving
