"""Lists of a kind and what is on for an occasion (11-03 practice set C, run 8).

"Are there any honor societies" was given 25 of the 31 and the reply named 5; "anything for black
solidarity week" never reached the calendar, whose two events for it the reply missed.
"""

import asyncio
import json
from datetime import datetime

import pytest

from rockygpt_brain import one_call
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.events import (
    asked_topic,
    asks_events,
    asks_occasion,
    kind_named,
    members_of,
    names_events,
    of_shared_kind,
    only_kind_asked,
)
from rockygpt_brain.provider import Completion, ToolCall, Usage
from rockygpt_brain.turn import intake
from test_engine import ScriptedGateway
from test_kinds import EASTERN, club_calendar

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=EASTERN)
HONOR = [("Honor Societies", "Honor Society - Academic"),
         ("Sigma Tau Delta", "Honor Society"), ("Alpha Kappa Delta", "Honor Society"),
         ("Psi Chi Honors Society", "Honor Society - Professional"),
         ("Chess Club", "Student Organization")]
INACTIVE = {"state": "inactive", "as_of": "2025-06-12", "evidence": [
    {"collection": "office_sections", "source_key": "office-pages", "field": "status",
     "source_record_key": "www.ramapo.edu/clubs/organization/psi-chi/",
     "source_url": "https://www.ramapo.edu/clubs/organization/psi-chi/"}]}


@pytest.mark.parametrize("asked", [
    "anything for black solidarity week",
    "Is there anything for Black Solidarity Week?",
    "What's planned for Black Solidarity Week?",
    "How does Ramapo celebrate Black Solidarity Week?",
    "How is Pride Month celebrated at Ramapo?",
    "what does ramapo do for black history month",
    "anything special for veterans day",
])
def test_what_is_on_for_an_occasion_is_the_calendars(asked: str) -> None:
    assert asks_occasion(asked) and asks_events(asked) and names_events(asked), asked


@pytest.mark.parametrize("asked", [
    "is the library open during finals week",
    "is housing open during thanksgiving week",
    "meal plans for one week",
    "is there anything for the week of thanksgiving",
    "do I need anything for the first day",
    "can I drop a class during the first week",
    "how much is parking per month",
])
def test_a_time_or_a_service_is_no_occasion(asked: str) -> None:
    assert not asks_occasion(asked) and not asks_events(asked), asked


def test_words_asking_for_an_answer_are_not_what_is_asked() -> None:
    # 11-03: "list the cultural clubs" was read as clubs about "list" and "cultural".
    assert asked_topic("list the cultural clubs") == {"cultural"}
    assert asked_topic("show me art events this week") == {"art"}
    assert asked_topic("which cultural clubs exist") == {"cultural"}
    assert asked_topic("can you list the honor societies") == {"honor"}
    assert asked_topic("what honor societies are there and how do I join one") == {"honor"}
    # Alone, such a word is what is asked: "any shows" asks for shows.
    assert asked_topic("any shows this weekend") == {"show"}


def directory() -> list[dict[str, str]]:
    return [{"id": f"c{n}", "name": name, "category": category}
            for n, (name, category) in enumerate(HONOR)]


def test_an_entry_named_for_its_kind_stands_for_the_clubs_listed_under_it() -> None:
    entries = directory()
    umbrella = kind_named("are there any honor societies", entries)
    assert umbrella is not None and umbrella["name"] == "Honor Societies"
    assert [e["name"] for e in members_of(umbrella, entries)] == [
        "Sigma Tau Delta", "Alpha Kappa Delta", "Psi Chi Honors Society"]
    assert kind_named("is there an honor society", entries) == umbrella  # Any form of it.
    assert only_kind_asked("list the honor societies", umbrella)
    assert not only_kind_asked("are there any honor societies for nursing", umbrella)
    # A club named beside it, or a club of its own name, is no kind asked.
    assert kind_named("is sigma tau delta one of the honor societies", entries) is None
    assert kind_named("is there a chess club", entries) is None


def test_every_club_of_a_kind_is_listed_by_code_with_what_a_page_marks_inactive() -> None:
    engine = ChatEngine(ScriptedGateway(), club_calendar(HONOR, {3: INACTIVE}),
                        output="written")
    listed = engine._kind_list(None, "are there any honor societies", 0)
    assert listed is not None
    said, sources, clubs, facts = listed
    assert said == (
        "Yes. The club directory lists 3 clubs under Honor Society [1]:\n\n"
        "- Alpha Kappa Delta [2]\n"
        "- Psi Chi Honors Society [3], but Ramapo's page for it says it is not currently an "
        "active club, as of June 12, 2025 [5]\n"
        "- Sigma Tau Delta [4]")
    assert [s["n"] for s in sources] == [1, 2, 3, 4, 5]
    assert sources[4]["url"] == "https://www.ramapo.edu/clubs/organization/psi-chi/"
    assert [c["n"] for c in clubs] == [2, 3, 4]
    assert facts[0] == {"fact": "The club directory lists 3 clubs under Honor Society.", "n": 1}
    # No kind asked, or more than the kind: no list (the reply picks "for nursing").
    assert engine._kind_list(None, "is there a chess club", 0) is None
    assert engine._kind_list(None, "are there any honor societies for nursing", 0) is None


def test_a_kind_asked_and_nothing_else_is_answered_by_code() -> None:
    gateway = ScriptedGateway()  # Any model call would fail: none is scripted.
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "what honor societies are there"}]})
    engine = ChatEngine(gateway, club_calendar(HONOR), output="written")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    assert result.body["answer"].startswith(
        "The club directory lists 3 clubs under Honor Society [1]:\n\n- Alpha Kappa Delta [2]\n"
        "- Psi Chi Honors Society [3]\n- Sigma Tau Delta [4]")
    assert gateway.inputs == [] and result.trace is not None
    assert result.trace["decidedBy"] == "code"


def test_a_kind_asked_with_more_starts_the_reply_with_codes_list() -> None:
    reply = {"parts": [{"asked": "how to join", "not_on_pages": True, "sections": []}],
             "text": "Each society's page says who may join. [3]"}
    gateway = ScriptedGateway(Completion("", (ToolCall("w", "write_answer", reply),), [
        {"type": "function_call", "call_id": "w", "name": "write_answer",
         "arguments": json.dumps(reply)}], Usage(10, 10, 0)))
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "what honor societies are there and how do I join one"}]})
    engine = ChatEngine(gateway, club_calendar(HONOR), output="written")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    given = json.loads(gateway.inputs[0][1]["content"])
    assert given["already_written"].startswith(
        "The club directory lists 3 clubs under Honor Society [1]:")
    # The model is given the clubs listed, and no club the question names again.
    assert [c["name"] for c in given["clubs"]] == [
        "Alpha Kappa Delta", "Psi Chi Honors Society", "Sigma Tau Delta"]
    assert result.body["answer"].startswith(
        "The club directory lists 3 clubs under Honor Society [1]:\n\n- Alpha Kappa Delta [2]")
    assert "Each society's page says who may join. [3]" in result.body["answer"]


def test_a_kinds_events_are_the_models_to_say_from_its_clubs() -> None:
    reply = {"parts": [{"asked": "honor society events", "not_on_pages": True, "sections": []}],
             "text": "The calendar lists no honor society events this week."}
    gateway = ScriptedGateway(Completion("", (ToolCall("w", "write_answer", reply),), [
        {"type": "function_call", "call_id": "w", "name": "write_answer",
         "arguments": json.dumps(reply)}], Usage(10, 10, 0)))
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "any honor society events this week"}]})
    engine = ChatEngine(gateway, club_calendar(HONOR), output="written")
    asyncio.run(engine.answer(intake(request, now=NOW), request))
    given = json.loads(gateway.inputs[0][1]["content"])
    assert "clubs under Honor Society" not in given.get("already_written", "")
    assert [c["name"] for c in given["clubs"]] == [
        "Alpha Kappa Delta", "Psi Chi Honors Society", "Sigma Tau Delta"]
    # Code wrote the calendar's part, and the model is told so (run 11: "any movie screenings"
    # said the calendar's answer twice, the second time in the model's words).
    assert given["already_written"].startswith("The calendar lists no event this week")
    assert given["calendar"]["said_by_code"] is True
    assert "calendar.said_by_code is true" in gateway.inputs[0][0]["content"]


def test_clubs_found_by_meaning_keep_the_kind_most_of_them_share() -> None:
    # 11-03 practice: "is there a ballet club" found three dance clubs and the Balkan Club, a
    # cultural club whose card scored near theirs.
    found = [{"id": "d1", "name": "Dance Company"}, {"id": "d2", "name": "Naach"},
             {"id": "d3", "name": "RCNJ Dance Team"}, {"id": "b", "name": "Balkan Club"}]
    kinds = {"d1": ("dance",), "d2": ("dance", "culture"), "d3": ("dance", "athletics"),
             "b": ("culture",)}
    assert [c["name"] for c in of_shared_kind(found, kinds)] == [
        "Dance Company", "Naach", "RCNJ Dance Team"]
    # Two clubs, no kind most share, or no kinds at all: every club found stays.
    assert of_shared_kind(found[2:], kinds) == found[2:]
    mixed = {"d1": ("dance",), "d2": ("music",), "d3": ("games",), "b": ("culture",)}
    assert of_shared_kind(found, mixed) == found
    assert of_shared_kind(found, None) == found


def test_what_code_wrote_is_not_said_again_by_the_model() -> None:
    # 11-03 practice: a film question's answer came twice, code's list and the model's copy of it.
    code = ("The calendar lists no movie and film events in the next 14 days, Tuesday, November 3 "
            "to Tuesday, November 17 [1].\n\nLater on the calendar:\n\nThursday, December 3:\n"
            "- 1 PM to 2 PM: Internal Movie Night [2]")
    copy = code.replace("movie and film", "movie or film")
    assert one_call.without_repeats(copy, [code]) == ("", [
        "The calendar lists no movie or film events in the next 14 days, Tuesday, November 3 to "
        "Tuesday, November 17 [1].", "Later on the calendar:", "Thursday, December 3:",
        "- 1 PM to 2 PM: Internal Movie Night [2]"])
    left, _ = one_call.without_repeats(f"{copy}\n\nTickets are free at the door. [3]", [code])
    assert left == "Tickets are free at the door. [3]"
    # A statement that says anything code did not stays.
    other = "The calendar lists no movie events this week [1]."
    assert one_call.without_repeats(other, [code]) == (other, [])


def test_a_models_copy_of_codes_list_is_dropped_from_the_reply() -> None:
    copy = ("The club directory lists 3 clubs under Honor Society [1]:\n\n- Alpha Kappa Delta [2]\n"
            "- Psi Chi Honors Society [3]\n- Sigma Tau Delta [4]\n\n"
            "Each society's page says who may join. [3]")
    reply = {"parts": [{"asked": "how to join", "not_on_pages": True, "sections": []}],
             "text": copy}
    gateway = ScriptedGateway(Completion("", (ToolCall("w", "write_answer", reply),), [
        {"type": "function_call", "call_id": "w", "name": "write_answer",
         "arguments": json.dumps(reply)}], Usage(10, 10, 0)))
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "what honor societies are there and how do I join one"}]})
    engine = ChatEngine(gateway, club_calendar(HONOR), output="written")
    result = asyncio.run(engine.answer(intake(request, now=NOW), request))
    answer = result.body["answer"]
    assert answer.count("Alpha Kappa Delta") == 1
    assert answer.startswith("The club directory lists 3 clubs under Honor Society [1]:")
    assert "Each society's page says who may join. [3]" in answer
    assert result.trace is not None and len(result.trace["oneCallRepeats"]) == 4
