"""Varsity teams' coaches, next games and results, said by code (teams.py)."""

import asyncio
from dataclasses import asdict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from rockygpt_brain.asked_time import AskedTime
from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval.entity_facts import MemoryEntityFacts
from rockygpt_brain.teams import answer, asked, teams_named
from rockygpt_brain.turn import intake
from test_engine import ScriptedGateway, completion, finish
from test_triage import ask, office, read

EASTERN = ZoneInfo("America/New_York")
TUESDAY = datetime(2026, 11, 3, 16, 0, tzinfo=EASTERN)
SITE = "https://ramapoathletics.com/sports"


def game(day: str, label: str, site: str, opponent: str, result: str = "", time: str = "",
         venue: str = "", location: str = "", note: str = "") -> dict[str, Any]:
    return {"date": day, "date_label": label, "time": time, "site": site, "opponent": opponent,
            "location": location, "venue": venue, "outcome": result[:1], "result": result,
            "note": note, "conference": False}


def team(slug: str, name: str, season: str, games: list[dict[str, Any]],
         record: dict[str, str] | None = None,
         coaches: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"source_key": "athletics-schedules", "source_record_key": slug, "name": name,
            "season": season, "record": record or {}, "games": games, "coaches": coaches or [],
            "source_url": f"{SITE}/{slug}/schedule", "coaches_url": f"{SITE}/{slug}/coaches",
            "schedule_collected_at": "2026-11-03T12:00:00Z"}


MSOC = team("mens-soccer", "Men's Soccer", "2026", [
    game("2026-10-28", "Oct 28 (Wed)", "away", "New Paltz", "L, 1-4", location="New Paltz, NY",
         venue="North Turf Field"),
    game("2026-10-31", "Oct 31 (Sat)", "away", "#3 The College of NJ", "L, 0-1",
         location="Ewing, NJ", venue="Lion's Stadium", note="NJAC Quarterfinal")],
    {"Overall": "8-7-3", "Conf": "3-4-2"},
    [{"name": "DJ Pinton", "title": "Head Coach", "email": "dpinton@ramapo.edu",
      "phone": "201-684-7198"},
     {"name": "Peter Bonilla", "title": "Assistant Coach", "email": "", "phone": ""}])
WSOC = team("womens-soccer", "Women's Soccer", "2026", [], {"Overall": "10-6"})
WBB = team("womens-basketball", "Women's Basketball", "2026-2027", [
    game("2026-11-07", "Nov 7 (Sat)", "home", "The City College of New York", time="1:00 PM",
         location="Mahwah, NJ", venue="Bradley Center"),
    game("2026-11-09", "Nov 9 (Mon)", "home", "St. Joseph's University Long Island",
         time="5:30 PM", location="Mahwah, NJ", venue="Bradley Center")],
    {"Overall": "0-0"})
TEAMS = [MSOC, WSOC, WBB]


def test_a_team_is_named_by_its_sport_and_men_or_women() -> None:
    def names(message: str) -> list[str]:
        return [t["name"] for t in teams_named(message, TEAMS)]
    assert names("who coaches the men's soccer team") == ["Men's Soccer"]
    assert names("womens soccer record") == ["Women's Soccer"]
    assert names("how is soccer doing") == ["Men's Soccer", "Women's Soccer"]
    assert names("when is the next girls hoops game") == ["Women's Basketball"]
    assert names("where is the library") == []


def test_what_is_asked_and_what_is_left_to_the_model() -> None:
    assert asked("who coaches men's soccer?") == ["coaches"]
    assert asked("when is the next women's basketball game?") == ["next"]
    assert asked("how did men's soccer do in their last game") == ["results"]
    assert asked("what's the soccer team's record") == ["results"]
    for other in ("is there a club volleyball team", "can I try out for soccer",
                  "is there an intramural basketball league", "is there a soccer team"):
        assert asked(other) == [], other


def test_coaches_are_said_with_how_to_reach_them() -> None:
    found = answer("Who coaches men's soccer?", TEAMS, TUESDAY.date())
    assert found is not None
    text, sources, records, _ = found
    assert text == ("Men's Soccer coaches: DJ Pinton, Head Coach (dpinton@ramapo.edu, "
                    "201-684-7198); Peter Bonilla, Assistant Coach [2].")
    assert [s["url"] for s in sources] == [f"{SITE}/mens-soccer/schedule",
                                           f"{SITE}/mens-soccer/coaches"]
    assert [r["name"] for r in records] == ["Men's Soccer"]


def test_next_games_start_today_and_a_finished_season_gives_its_results() -> None:
    found = answer("When is the next women's basketball game?", TEAMS, TUESDAY.date())
    assert found is not None and found[0] == (
        "Women's Basketball's next games: Nov 7 (Sat), 1:00 PM: vs The City College of New York, "
        "Bradley Center, Mahwah, NJ (home); Nov 9 (Mon), 5:30 PM: vs St. Joseph's University "
        "Long Island, Bradley Center, Mahwah, NJ (home) [1].")
    found = answer("When does men's soccer play next?", TEAMS, TUESDAY.date())
    assert found is not None and found[0] == (
        "Men's Soccer has no more games on its 2026 schedule [1]. Men's Soccer's 2026 record is "
        "8-7-3 (3-4-2 in the conference) [1]. Last games: Oct 31 (Sat): at #3 The College of NJ "
        "(NJAC Quarterfinal), Lion's Stadium, Ewing, NJ. Result: L, 0-1; Oct 28 (Wed): at New "
        "Paltz, North Turf Field, New Paltz, NY. Result: L, 1-4 [1].")


def test_a_tournament_round_still_to_be_drawn_is_not_said_as_an_opponent() -> None:
    wvb = team("womens-volleyball", "Women's Volleyball", "2026", [
        game("2026-11-10", "Nov 10 (Tue)", "home", "NJAC Tournament Quarterfinal", time="TBA",
             location="TBA"),
        game("2026-11-14", "Nov 14 (Sat)", "away", "NJAC Championship Match", time="TBA"),
        game("2026-11-15", "Nov 15 (Sun)", "home", "Kean University", time="1:00 PM")])
    found = answer("when is the next women's volleyball game", [wvb], date(2026, 11, 4))
    assert found is not None
    text = found[0]
    assert "Nov 10 (Tue), TBA: NJAC Tournament Quarterfinal, TBA (home)" in text
    assert "vs NJAC" not in text
    assert "Nov 14 (Sat), TBA: at NJAC Championship Match" in text
    assert "vs Kean University" in text


def test_an_old_capture_is_said_with_its_date() -> None:
    found = answer("men's soccer record", [MSOC], date(2026, 11, 9))
    assert found is not None and found[0].endswith("(As the athletics site listed it on Nov 3.)")


def facts() -> MemoryEntityFacts:
    entities = [{"id": f"team-{t['source_record_key']}", "name": t["name"], "kind": "team",
                 "aliases": [], "links": [{"collection": "teams",
                                           "source_key": "athletics-schedules",
                                           "source_record_keys": [t["source_record_key"]]}]}
                for t in TEAMS]
    return MemoryEntityFacts(dataset_version="release-1", identity_hash="identities-1",
                             entities=entities, contacts=[], records={"teams": TEAMS},
                             now=lambda: TUESDAY)


def test_a_coach_question_is_answered_by_code_with_no_model_call() -> None:
    gateway = ScriptedGateway()  # Any model call would fail: none is scripted.
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "who is the men's soccer coach?"}]})
    engine = ChatEngine(gateway, facts(), output="written")
    result = asyncio.run(engine.answer(intake(request, now=TUESDAY), request))
    assert result.body["answer"] == (
        "Men's Soccer coaches: DJ Pinton, Head Coach (dpinton@ramapo.edu, 201-684-7198); "
        f"Peter Bonilla, Assistant Coach [2].\n\nSources: [2]({SITE}/mens-soccer/coaches)")
    assert gateway.inputs == [] and result.trace is not None
    assert result.trace["decidedBy"] == "code"


def test_a_plain_team_record_question_does_not_need_a_read() -> None:
    # 11-04 live check: the read called "how did men's soccer do this season" sports scores and
    # said it only knows about Ramapo. The whole question asks for the named team's record.
    gateway = ScriptedGateway()  # The read is a model call: none is scripted.
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "how did men's soccer do this season"}]})
    engine = ChatEngine(gateway, facts(), output="written", read_first=True)
    result = asyncio.run(engine.answer(intake(request, now=TUESDAY), request))
    assert gateway.inputs == [] and result.trace is not None
    assert result.trace["decidedBy"] == "code"
    assert "8-7-3" in result.body["answer"]


def test_a_plain_unqualified_game_lookup_still_needs_no_model_call() -> None:
    gateway = ScriptedGateway()
    result = ask(gateway, "When is the next women's basketball game?", service=facts(), now=TUESDAY)
    assert "The City College of New York" in result.body["answer"]
    assert gateway.inputs == []


@pytest.mark.parametrize(("venue", "expected"), [
    ("home", "The City College of New York"),
    ("away", "no more away games"),
])
def test_a_game_question_uses_the_readers_venue(venue: str, expected: str) -> None:
    message = f"When is the next {venue} women's basketball game?"
    gateway = ScriptedGateway(read(question=message, topic="team",
                                   time=asdict(AskedTime(home_away=venue))))
    result = ask(gateway, message, service=facts(), now=TUESDAY)
    assert result.body["status"] == "answered" and expected in result.body["answer"]
    assert result.trace is not None and result.trace["decidedBy"] == "code"
    assert len(gateway.inputs) == 1 and result.trace["read"]["time"]["home_away"] == venue
    if venue == "away":
        assert "Bradley Center" not in result.body["answer"]


def test_a_parking_question_naming_a_game_reaches_the_parking_office() -> None:
    service = facts()
    entity, contact = office("parking", "Parking Office", "parking@example.edu", "+12015550177")
    service.entities.append(entity)
    service.contacts.append({**contact, "collected_at": TUESDAY})
    message = "Who do I contact about parking when there's a women's basketball game?"
    gateway = ScriptedGateway(read(question=message, topic="how_to"), completion(
        "graph_lookup", {"requests": [{"query": "Parking Office", "fields": ["email"]}]}),
        finish())
    result = ask(gateway, message, service=service, now=TUESDAY)
    assert result.trace is not None and result.trace["path"]["topic"] == "how_to"
    assert "parking@example.edu" in result.body["answer"]
    assert "Bradley Center" not in result.body["answer"]
    assert [source["url"] for source in result.body["citations"]] == ["https://example.edu/parking"]


def test_a_team_lookup_cannot_discard_a_venue_it_never_read() -> None:
    from test_one_call import one_reply

    message = "When is the next away women's basketball game?"
    gateway = ScriptedGateway(read(question=message, topic="team", time=None),
                              one_reply("The calendar has no listing for that game.", [], True))
    result = ask(gateway, message, service=facts(), now=TUESDAY)
    assert "Bradley Center" not in result.body["answer"]
    assert all(source["url"] != WBB["source_url"] for source in result.body["citations"])
    assert result.trace is not None and result.trace.get("decidedBy") != "code"


def test_a_team_lookup_keeps_a_time_constraint_and_says_none_fits() -> None:
    # Plan 11-19 (Dan chose "Build Steps 0 to 2 on dev"): the team lookup filters by the days
    # read; with no game then it says so, then the next game, labelled with its own day.
    message = "Is there a women's basketball game tomorrow?"
    gateway = ScriptedGateway(read(question=message, topic="team",
                                   time=asdict(AskedTime(when="tomorrow"))))
    result = ask(gateway, message, service=facts(), now=TUESDAY)
    answer = result.body["answer"]
    assert answer.startswith("Women's Basketball has no game listed tomorrow, Wednesday, "
                             "November 4 [1]. Its next game: Nov 7 (Sat), 1:00 PM")
    assert result.trace is not None and result.trace["decidedBy"] == "code"
    assert result.trace["conditions"]["used"] == ["days"]
