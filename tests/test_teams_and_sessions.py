"""Teams students can join and sessions they ask for (11-03 practice set C).

"Is there a hockey team" was answered from an admissions page, without the club directory's Ice
Hockey Club; "any study sessions" never reached the calendar.
"""

import asyncio
import json

import pytest

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.events import (
    asked_topic,
    asks_club_exists,
    asks_clubs,
    asks_events,
    asks_sessions,
    asks_team,
    club_named,
    names_events,
)
from rockygpt_brain.provider import Completion, ToolCall, Usage
from rockygpt_brain.turn import intake
from test_engine import ScriptedGateway
from test_events import INACTIVE, WEDNESDAY, calendar


@pytest.mark.parametrize("asked", [
    "is there a hockey team",
    "Does Ramapo have a cheerleading team?",
    "are there any club sports teams",
    "how do I join the soccer team",
    "when are tryouts for the dance team",
])
def test_a_team_a_student_can_join_is_a_club_and_its_games(asked: str) -> None:
    assert asks_team(asked) and asks_clubs(asked), asked
    # A team's games are what it does: the calendar is read beside the club directory.
    assert asks_events(asked), asked


@pytest.mark.parametrize("asked", [
    "who is on the IT team",
    "how do I contact the admissions team",
    "my team project is due, where can I print",
])
def test_a_team_that_is_no_club_asks_for_neither(asked: str) -> None:
    assert not asks_team(asked) and not asks_clubs(asked) and not asks_events(asked), asked


def test_team_and_join_say_no_topic() -> None:
    assert asked_topic("is there a hockey team") == {"hockey"}
    assert asked_topic("how do I join the soccer team") == {"soccer"}


def test_a_team_names_its_club_sports_entry_but_a_club_never_names_a_team() -> None:
    clubs = [{"id": "h", "name": "Ramapo College Ice Hockey Club"},
             {"id": "d", "name": "RCNJ Dance Team"}, {"id": "c", "name": "Chess Club"}]
    assert club_named("Is there an ice hockey team?", clubs) == clubs[0]
    assert club_named("is there a dance team", clubs) == clubs[1]
    assert club_named("is there a chess team", clubs) == clubs[2]
    assert club_named("are there any dance clubs", clubs) is None  # A kind, not the Dance Team.
    assert club_named("is there a hockey team", clubs) is None  # Not every word of its name.
    assert asks_club_exists("is there a hockey team")


def test_whether_a_team_exists_is_codes_like_a_clubs() -> None:
    gateway = ScriptedGateway()  # Any model call would fail: none is scripted.
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "Is there a chess team?"}]})
    engine = ChatEngine(gateway, calendar(club_status=INACTIVE), output="written")
    result = asyncio.run(engine.answer(intake(request, now=WEDNESDAY), request))
    assert result.body["answer"].startswith(
        "The club directory lists Chess Club (Recreation) [1], but Ramapo's page for it says it "
        "is not currently an active club")
    assert gateway.inputs == [] and result.trace is not None
    assert result.trace["decidedBy"] == "code"


@pytest.mark.parametrize("asked", [
    "any study sessions",
    "are there any tutoring sessions this week",
    "is there a therapy session",
    "are there sessions for transfer students",
])
def test_sessions_asked_for_read_the_calendar(asked: str) -> None:
    assert asks_sessions(asked) and asks_events(asked) and names_events(asked), asked


@pytest.mark.parametrize("asked", [
    "is school in session on veterans day",
    "any summer sessions",
    "when does winter session start",
    "where do I book a counseling session",
])
def test_a_term_or_an_appointment_is_no_calendar_session(asked: str) -> None:
    assert not asks_sessions(asked) and not asks_events(asked), asked


def test_sessions_are_written_with_the_pages_beside_the_calendar() -> None:
    # "any trivia nights" is the calendar's alone, and code lists it.
    gateway = ScriptedGateway()  # Any model call would fail: none is scripted.
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "any trivia nights"}]})
    engine = ChatEngine(gateway, calendar(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=WEDNESDAY), request))
    assert gateway.inputs == [] and result.trace is not None
    assert result.trace["decidedBy"] == "code"
    # Sessions may be an office's (tutoring, counseling): the model reads them with the pages.
    reply = {"parts": [{"asked": "trivia sessions", "not_on_pages": False, "sections": [1]}],
             "text": "Trivia Night is Fri, Oct 23, 2026 at 8 PM in the Student Center. [2]"}
    gateway = ScriptedGateway(Completion("", (ToolCall("w", "write_answer", reply),), [
        {"type": "function_call", "call_id": "w", "name": "write_answer",
         "arguments": json.dumps(reply)}], Usage(10, 10, 0)))
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "are there any trivia sessions"}]})
    engine = ChatEngine(gateway, calendar(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=WEDNESDAY), request))
    given = json.loads(gateway.inputs[0][1]["content"])
    assert [line["event"] for line in given["calendar"]["events"]] == ["Trivia Night"]
    assert result.trace is not None and result.trace["decidedBy"] == "one_call"


def test_a_word_only_in_a_description_counts_when_the_cards_meaning_agrees() -> None:
    # 11-03 practice: "any coding workshops" found a therapy animal visit, whose description says
    # to scan the QR code: "coding" and "code" share a plain form, and the thing is different.
    from rockygpt_brain.events import rank_events
    from test_kinds import END, START, event
    calendar = [event(1, "Therapy Animal Visit", 5, "Please scan the QR code to check in."),
                event(2, "Intro to Coding Workshop", 6, "Build a web page."),
                event(3, "Python Night", 7, "Learn coding with friends.")]
    scores = {f"x{n}": 0.20 + n / 1000 for n in range(30)} | {"e1": 0.18, "e2": 0.62,
                                                               "e3": 0.57}
    ranked, matched = rank_events(calendar, "any coding workshops", START, END, scores, calendar)
    assert matched is True
    # The title names it; the description says it and the card's meaning agrees.
    assert [v["title"] for v in ranked] == ["Intro to Coding Workshop", "Python Night"]
    # Without scores the words alone decide, as before.
    words, _ = rank_events(calendar, "any coding workshops", START, END, None, calendar)
    assert len(words) == 3


def test_a_club_is_given_kinds_by_its_main_purpose() -> None:
    from rockygpt_brain.kinds import BY_KEY, KINDS_VERSION, RULES
    assert "main purpose" in RULES and "locks out a room" in RULES
    # Esports and watch parties are no sports; politics is not every cause.
    assert "Not esports" in BY_KEY["athletics"].holds
    assert "activism" not in BY_KEY["civic"].named and "activism" in BY_KEY["advocacy"].named
    assert "not abroad" in BY_KEY["abroad"].holds
    assert len(KINDS_VERSION) == 16


def test_only_the_nearest_clubs_are_said_by_code_as_the_nearest() -> None:
    # 11-03 practice: "is there a club for comic books" was answered "Yes" from the nearest clubs.
    from rockygpt_brain.events import nearest_clubs_text
    clubs = [{"n": 2, "name": "Geek and Nerd Culture", "category": "Special Interest"},
             {"n": 3, "name": "The Fantasy Film and Novel Society"}]
    assert nearest_clubs_text(clubs, "is there a club for comic books") == (
        "The club directory lists no club named for comic books. The nearest, by what their "
        "entries describe: Geek and Nerd Culture (Special Interest) [2]; The Fantasy Film and "
        "Novel Society [3].")
    assert nearest_clubs_text(clubs[:1], "Is there a club for anime and manga?") == (
        "The club directory lists no club named for anime and manga. The nearest, by what its "
        "entry describes: Geek and Nerd Culture (Special Interest) [2].")
    # A club named for a word asked is the model's to describe; with none, nothing is cited.
    assert nearest_clubs_text([{"n": 2, "name": "Comic Book Club"}],
                              "is there a club for comic books") is None
    assert nearest_clubs_text([], "is there a club for comic books") is None
