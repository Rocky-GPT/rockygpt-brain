"""Varsity teams, answered by code from each team's record (rockygpt-data athletics.ts).

The data release reads the college's athletics site (ramapoathletics.com): every team's schedule
page (games, places, results, season record) each day and its coaches page each week. A question
that names a team ("who coaches men's soccer", "when is the next women's basketball game", "how
did field hockey do") and asks only for its coaches, its next games or its results is said by
code from that record, with no model call (Dan 11-03: code writes as much of each answer as it
can). Anything else about a team is left to the model.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

# A sport by the words students use for it, keyed by the words of its team names.
_SPORTS = (
    ("soccer", re.compile(r"\bsoccer\b")),
    ("basketball", re.compile(r"\b(?:basketball|hoops|bball|b-ball)\b")),
    ("cross country", re.compile(r"\b(?:cross[ -]?country|xc)\b")),
    ("swimming", re.compile(r"\b(?:swim|swims|swimming|swimmers?|diving|divers?|dive)\b")),
    ("track", re.compile(r"\btrack\b(?! (?:my|your|the|a|an|down)\b)")),
    ("volleyball", re.compile(r"\b(?:volleyball|vball|v-ball)\b")),
    ("baseball", re.compile(r"\bbaseball\b")),
    ("softball", re.compile(r"\bsoftball\b")),
    ("lacrosse", re.compile(r"\b(?:lacrosse|lax)\b")),
    ("field hockey", re.compile(r"\bfield hockey\b")),
)
_MEN = re.compile(r"\b(?:men|mens|man|boys|guys|male)\b")
_WOMEN = re.compile(r"\b(?:women|womens|woman|girls|ladies|female|lady)\b")

_COACHES = re.compile(r"\b(?:coach|coaches|coached|coaching|coachs)\b")
_NEXT = re.compile(r"\b(?:next|upcoming|schedule|schedules|when|tonight|today|tomorrow|weekend|"
                   r"this week|next week)\b")
_GAMES = re.compile(r"\b(?:game|games|match|matches|meet|meets)\b")
# A question about playing a sport oneself, or a club or intramural team, is not a varsity
# team's schedule: "is there a club volleyball team", "can I try out for soccer".
_NOT_VARSITY = re.compile(r"\b(?:club|clubs|intramural|intramurals|join|try ?out|tryouts?|"
                          r"walk ?on|recreational|pickup|pick up)\b")
_RESULTS = re.compile(r"\b(?:score|scores|result|results|won|win|wins|winning|lose|lost|loss|"
                      r"losses|beat|record|doing|did|last|standings?)\b")
# The most games said of one kind (next or last) for one team.
MAX_GAMES = 3
# A record older than this is said with its date: results change as games are played.
STALE_DAYS = 2


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9-]+", text.casefold().replace("’", "'").replace("'", "")))


def _sport(name: str) -> str | None:
    words = _words(name)
    return next((key for key, pattern in _SPORTS if pattern.search(words)), None)


def _gender(text: str) -> str | None:
    words = _words(text)
    if _WOMEN.search(words):
        return "women"
    if _MEN.search(words):
        return "men"
    return None


def teams_named(message: str, teams: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The teams a message names: by sport, and by men's or women's when it says one. A sport
    both have, said with neither ("soccer"), names both."""
    words = _words(message)
    sports = {key for key, pattern in _SPORTS if pattern.search(words)}
    if not sports:
        return []
    gender = _gender(message)
    found = [t for t in teams if _sport(str(t.get("name") or "")) in sports]
    gendered = [t for t in found if _gender(str(t.get("name") or "")) in (gender, None)]
    return gendered if gender and gendered else found


def asked(message: str) -> list[str]:
    """What a team question asks: its coaches, its next games, its results (in that order). A
    game asked about with no word of results ("when do they play") asks the next ones."""
    words = _words(message)
    if _NOT_VARSITY.search(words):
        return []
    asks = ["coaches"] if _COACHES.search(words) else []
    results = bool(_RESULTS.search(words))
    if _NEXT.search(words) or (_GAMES.search(words) and not results):
        asks.append("next")
    if results:
        asks.append("results")
    return asks


def _game_line(game: Mapping[str, Any], played: bool) -> str:
    side = {"away": "at", "home": "vs", "neutral": "vs"}.get(str(game.get("site")), "vs")
    when = str(game.get("date_label") or "")
    if not played and game.get("time"):
        when += f", {game['time']}"
    place = ", ".join(p for p in (str(game.get("venue") or ""), str(game.get("location") or ""))
                      if p)
    line = f"{when}: {side} {game.get('opponent')}"
    if game.get("note"):
        line += f" ({game['note']})"
    if place:
        line += f", {place}"
    if game.get("site") == "home":
        line += " (home)"
    if played and game.get("result"):
        line += f". Result: {game['result']}"
    return line


def _dated(game: Mapping[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(game.get("date") or ""))
    except ValueError:
        return None


def team_text(team: Mapping[str, Any], asks: Sequence[str], today: date,
              schedule_n: int, coaches_n: int | None) -> str | None:
    """What code says of one team: the coaches, the next games or the results asked, each with
    its source's [n]. None when nothing asked is in the record."""
    name = str(team.get("name") or "The team")
    parts: list[str] = []
    if "coaches" in asks:
        coaches = [c for c in team.get("coaches") or [] if c.get("name")]
        if coaches and coaches_n is not None:
            listed = []
            for coach in coaches:
                reach = ", ".join(str(coach[k]) for k in ("email", "phone") if coach.get(k))
                listed.append(f"{coach['name']}, {coach.get('title') or 'Coach'}"
                              + (f" ({reach})" if reach else ""))
            parts.append(f"{name} coaches: " + "; ".join(listed) + f" [{coaches_n}].")
    games = [g for g in team.get("games") or [] if g.get("opponent")]
    dated = [(d, g) for g in games if (d := _dated(g)) is not None]
    upcoming = [g for d, g in dated if d >= today and not g.get("result")]
    played = [g for d, g in dated if d <= today and g.get("result")]
    season = str(team.get("season") or "")
    its = f"its {season} schedule" if season else "its schedule"
    if "next" in asks:
        if upcoming:
            lines = [_game_line(g, False) for g in upcoming[:MAX_GAMES]]
            parts.append(f"{name}'s next game{'s' if len(lines) > 1 else ''}: "
                         + "; ".join(lines) + f" [{schedule_n}].")
        elif games:
            parts.append(f"{name} has no more games on {its} [{schedule_n}].")
    if "results" in asks or ("next" in asks and not upcoming and played):
        record = team.get("record") or {}
        overall = record.get("Overall") or record.get("Overall Record")
        conference = record.get("Conf") or record.get("Conference")
        if overall:
            line = f"{name}'s {season} record is {overall}" if season else \
                f"{name}'s record is {overall}"
            if conference:
                line += f" ({conference} in the conference)"
            parts.append(line + f" [{schedule_n}].")
        if played:
            last = played[-MAX_GAMES:][::-1]
            parts.append(f"Last game{'s' if len(last) > 1 else ''}: "
                         + "; ".join(_game_line(g, True) for g in last) + f" [{schedule_n}].")
    if not parts:
        return None
    collected = str(team.get("schedule_collected_at") or team.get("collected_at") or "")
    try:
        read = datetime.fromisoformat(collected.replace("Z", "+00:00")).date()
    except ValueError:
        read = None
    if read is not None and (today - read).days >= STALE_DAYS and ("results" in asks
                                                                     or "next" in asks):
        parts.append(f"(As the athletics site listed it on {read.strftime('%b')} {read.day}.)")
    return " ".join(parts)


def answer(message: str, teams: Sequence[Mapping[str, Any]], today: date,
           ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Code's answer to a question naming at most two teams and asking their coaches, next games
    or results: the text, its sources and the team records it was written from. None for
    anything else (no team named, nothing of these asked, a record with none of it)."""
    named = teams_named(message, teams)
    asks = asked(message)
    if not named or len(named) > 2 or not asks:
        return None
    sources: list[dict[str, Any]] = []
    texts: list[str] = []
    for team in named:
        schedule_n = len(sources) + 1
        sources.append({"n": schedule_n, "url": team.get("source_url"),
                        "title": f"{team.get('name')} schedule (ramapoathletics.com)"})
        coaches_n = None
        if "coaches" in asks and team.get("coaches_url"):
            coaches_n = len(sources) + 1
            sources.append({"n": coaches_n, "url": team.get("coaches_url"),
                            "title": f"{team.get('name')} coaches (ramapoathletics.com)"})
        said = team_text(team, asks, today, schedule_n, coaches_n)
        if said:
            texts.append(said)
    if not texts:
        return None
    return "\n\n".join(texts), sources, [dict(t) for t in named]
