"""Varsity teams' coaches, next games and results, said by code (teams.py)."""

import asyncio
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval.entity_facts import MemoryEntityFacts
from rockygpt_brain.teams import answer, asked, teams_named
from rockygpt_brain.turn import intake
from test_engine import ScriptedGateway

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
    text, sources, records = found
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


def test_a_team_question_is_not_read_first_so_it_is_never_called_off_topic() -> None:
    # 11-04 live check: the read called "how did men's soccer do this season" sports scores and
    # said it only knows about Ramapo. A question naming one of the release's teams is code's.
    gateway = ScriptedGateway()  # The read is a model call: none is scripted.
    request = ChatRequest.model_validate({"messages": [
        {"role": "user", "content": "how did men's soccer do this season"}]})
    engine = ChatEngine(gateway, facts(), output="written", read_first=True)
    result = asyncio.run(engine.answer(intake(request, now=TUESDAY), request))
    assert gateway.inputs == [] and result.trace is not None
    assert result.trace["decidedBy"] == "code"
    assert "8-7-3" in result.body["answer"]
