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
    club_answers,
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
    # 11-03 run 10: "Trillium", a club entry with a name and no mission, was labelled LGBTQ+
    # from the name alone; the college's pages say it is the literary magazine.
    assert "only the listing's own words (its name, category" in RULES
    # A fan community is not a culture (11-03 run 12: Geek and Nerd Culture was listed cultural).
    assert "Not fan or hobby communities" in BY_KEY["culture"].holds
    # Esports and watch parties are no sports; politics is not every cause.
    assert "Not esports" in BY_KEY["athletics"].holds
    assert "activism" not in BY_KEY["civic"].named and "activism" in BY_KEY["advocacy"].named
    assert "not abroad" in BY_KEY["abroad"].holds
    # A club that makes animated work uses software, and is art (11-03 practice: "are there any
    # stem clubs" listed the Animation Production Club).
    assert "animation" in BY_KEY["arts"].holds and "Not art, music or film" in BY_KEY["tech"].holds
    assert len(KINDS_VERSION) == 16


def test_clubs_found_by_meaning_are_the_replys_to_judge_with_what_their_names_and_entries_say(
        ) -> None:
    # 11-03 practice: code's "lists no club for space" was wrong (the Astronomy Club is one), and
    # "is there an anime club" never said that no club is named for anime.
    geek = {"n": 2, "name": "Geek and Nerd Culture", "category": "Student Organization",
            "mission": "[PREVIOUSLY SCI FI AND COMIC BOOK CLUB] Fans of games, anime and films?"}
    stars = {"n": 3, "name": "Astronomy Club", "category": "Student Organization",
             "mission": "Learn about the universe, the stars and constellations."}
    facts = [f["fact"] for f in club_answers([geek, stars], "is there an anime club", False, 9)]
    assert facts[0] == ("No club in the directory has anime in its name; the entry of Geek and "
                        "Nerd Culture [2] says anime; the other clubs given are only the nearest "
                        "by meaning.")
    facts = [f["fact"] for f in club_answers(
        [stars], "is there a club for people interested in space", False, 9)]
    assert facts[0] == ("No club in the directory has space in its name; the clubs given are "
                        "only the nearest by meaning.")
    # The topic in the question's own words.
    facts = [f["fact"] for f in club_answers([stars], "is there a martial arts club", False, 9)]
    assert facts[0].startswith("No club in the directory has martial arts in its name")
    # A word asked in a club's name: the reply judges whether it is what was asked.
    midnight = {"n": 4, "name": "The Midnight Book Club", "category": "Student Organization"}
    assert not [f for f in club_answers([geek, midnight], "is there a club for comic books",
                                        False, 9) if "nearest" in f["fact"]]


def test_the_college_and_a_name_inside_a_longer_name_are_no_club_a_page_names() -> None:
    from rockygpt_brain.events import clubs_on_pages
    directory = [{"name": "Ramapo College of New Jersey"}, {"name": "Phi Alpha"},
                 {"name": "Phi Alpha Delta"}, {"name": "Order of Omega"}]
    page = ("Fraternity and Sorority Life at Ramapo College of New Jersey: Phi Alpha Delta is a "
            "pre-law fraternity, and members may join the Order of Omega.")
    assert [c["name"] for c in clubs_on_pages(directory, page)] == [
        "Phi Alpha Delta", "Order of Omega"]
    assert [c["name"] for c in clubs_on_pages(directory, "Phi Alpha is for social work.")] == [
        "Phi Alpha"]


def test_a_description_saying_what_was_asked_counts_when_its_card_is_clearly_near() -> None:
    # 11-03 practice: the cookie art and magnet decorating nights say "decorate" only in their
    # descriptions and sat two spreads above the median card; the stand-out bar (three) dropped
    # them. A birdwatching outing that provides books sat 1.2 spreads up for "book clubs".
    from rockygpt_brain.events import rank_events
    from test_kinds import END, START, event
    calendar = [event(1, "Gingerbread Decorating", 5),
                event(2, "Magnetic Moments", 6, "Make your own magnet to decorate."),
                event(3, "Birdwatching", 7, "Binoculars and books will be provided."),
                event(4, "Silent Book Club", 8)]
    # Every other card: median 0.345, one spread 0.037; the stand-out bar is 0.456.
    base = {f"x{n}": 0.30 + (n % 10) / 100 for n in range(40)}
    decorating = base | {"e1": 0.60, "e2": 0.43, "e3": 0.30, "e4": 0.31}  # Magnets: 2.3 up.
    ranked, _ = rank_events(calendar, "any decorating events", START, END, decorating, calendar)
    assert [v["title"] for v in ranked] == ["Gingerbread Decorating", "Magnetic Moments"]
    books = base | {"e1": 0.30, "e2": 0.31, "e3": 0.39, "e4": 0.62}  # Birdwatching: 1.2 up.
    ranked, _ = rank_events(calendar, "any book clubs meeting", START, END, books, calendar)
    assert [v["title"] for v in ranked] == ["Silent Book Club"]


def test_a_word_a_question_joins_to_a_clubs_word_is_said_with_it_or_its_card_stands_out(
        ) -> None:
    # 11-04 practice, dev calendar: the birdwatching outing whose description says "Binoculars
    # and books will be provided" came out above the description bar (SAID_AGREES) for "any
    # book clubs meeting", and was listed beside the silent book club. "Book clubs" names a
    # thing: a description saying "books" without "book club" counts as naming nothing asked.
    from rockygpt_brain.events import joined_words, rank_events
    from test_kinds import END, START, event
    assert joined_words("any book clubs meeting", {"book"}) == [("book", "club")]
    assert joined_words("black student union events", {"black"}) == [
        ("black", "student", "union")]
    assert joined_words("any decorating events", {"decorat"}) == []
    assert joined_words("clubs for books", {"book"}) == []
    calendar = [event(3, "Birdwatching", 7, "Binoculars and books will be provided."),
                event(4, "Silent Book Club", 8),
                event(5, "Monthly Meetup", 9, "Our book club reads one novel a month.")]
    # With these three cards, the description bar is 0.417 and the stand-out bar 0.483.
    base = {f"x{n}": 0.30 + (n % 10) / 100 for n in range(40)}
    books = base | {"e3": 0.42, "e4": 0.62, "e5": 0.42}
    ranked, _ = rank_events(calendar, "any book clubs meeting", START, END, books, calendar)
    assert [v["title"] for v in ranked] == ["Silent Book Club", "Monthly Meetup"]
    # A card that stands out for the question is listed, as one naming nothing asked would be.
    ranked, _ = rank_events(calendar, "any book clubs meeting", START, END,
                            books | {"e3": 0.50}, calendar)
    assert [v["title"] for v in ranked] == ["Birdwatching", "Silent Book Club", "Monthly Meetup"]
    # Without meaning scores, a word said in passing is not enough.
    ranked, _ = rank_events(calendar, "any book clubs meeting", START, END, None, calendar)
    assert [v["title"] for v in ranked] == ["Silent Book Club", "Monthly Meetup"]
    # Words asked with no club's word are matched as before.
    ranked, _ = rank_events(calendar, "any events about books", START, END, books, calendar)
    assert "Birdwatching" in [v["title"] for v in ranked]


def test_events_for_commuters_names_commuter_affairs() -> None:
    from rockygpt_brain.events import asked_topic, names_host
    assert names_host("Commuter Affairs", asked_topic("any events for commuters"))
    assert not names_host("Health Promotion", asked_topic("any health events"))
    assert not names_host("College Programming Board", asked_topic("any board game events"))
    # A group of students names the office or club for it (fix check 11-04).
    assert names_host("Women's Center & LGBTQ+ Services", asked_topic("any lgbtq events this week"))
    assert names_host("Chabad Jewish Student Club", asked_topic("any jewish events soon"))
    assert not names_host("Women's Center & LGBTQ+ Services", asked_topic("any service events"))


def test_a_kind_asked_lists_the_narrower_kinds_it_includes() -> None:
    # 11-03 practice: "events for international students" lost the study abroad sessions, and
    # "any study sessions" listed an honor society induction and a celebration.
    from rockygpt_brain.events import rank_events
    from rockygpt_brain.kinds import with_included
    from test_kinds import END, START, event
    assert with_included({"international"}) == {"international", "abroad"}
    assert with_included({"study"}) == {"study"}
    calendar = [event(1, "International Education Week", 5), event(2, "Study in Prague", 6),
                event(3, "Writing Center Hours", 7), event(4, "Honor Society Induction", 8)]
    kinds = {"e1": ["international"], "e2": ["abroad"], "e3": ["study"], "e4": ["academic"]}
    ranked, _ = rank_events(calendar, "any events for international students", START, END,
                            None, calendar, kinds=kinds)
    assert [v["title"] for v in ranked] == ["International Education Week", "Study in Prague"]
    ranked, _ = rank_events(calendar, "any study sessions", START, END, None, calendar,
                            kinds=kinds)
    assert [v["title"] for v in ranked] == ["Writing Center Hours"]
    ranked, _ = rank_events(calendar, "any academic events", START, END, None, calendar,
                            kinds=kinds)
    assert [v["title"] for v in ranked] == ["Writing Center Hours", "Honor Society Induction"]


def test_a_team_question_finds_no_event_by_meaning_alone() -> None:
    # 11-03 practice: an NFL watch party was the "closest event" to "is there a hockey team".
    from rockygpt_brain.events import rank_events
    from test_kinds import END, START, event
    calendar = [event(1, "Bills VS Vikings", 5, "Watch the game on the big screen."),
                event(2, "Men's Basketball vs. Kean", 6)]
    scores = {f"x{n}": 0.20 + n / 1000 for n in range(30)} | {"e1": 0.6, "e2": 0.5}
    ranked, matched = rank_events(calendar, "is there a hockey team", START, END, scores,
                                  calendar)
    assert matched is False
    near, _ = rank_events(calendar, "anything like hockey", START, END, scores, calendar)
    assert [v["title"] for v in near] == ["Bills VS Vikings"]  # Not a team question.


def test_a_kind_of_club_lists_the_clubs_of_the_kinds_it_includes() -> None:
    from rockygpt_brain.engine import ChatEngine
    from test_kinds import club_calendar

    class Index:
        kinds = {"c0": ("international",), "c1": ("abroad",), "c2": ("games",)}

    names = [("International Student Organization", "Student Organization"),
             ("Study Abroad Ambassadors", "Student Organization"),
             ("Chess Club", "Student Organization")]
    engine = ChatEngine(ScriptedGateway(), club_calendar(names), output="written")
    found = engine._clubs_by_kind(Index(), "are there any international clubs")  # type: ignore[arg-type]
    assert found is not None
    clubs, said = found
    assert [c["name"] for c in clubs] == ["International Student Organization",
                                          "Study Abroad Ambassadors"]
    assert said == "international"
