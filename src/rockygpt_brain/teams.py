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
from datetime import date, datetime, time, timedelta
from typing import Any

from rockygpt_brain import asked_time, conditions
from rockygpt_brain.asked_time import AskedTime, Window
from rockygpt_brain.conditions import Receipt

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
# Days ("tonight", "this weekend") are the read's to pick (asked_time.py), not words that ask for
# the next games: a team question naming days is read, and its games are those days' (11-19 plan:
# "is there a womens bball game tonight" listed the next three games and never said "not
# tonight").
_NEXT = re.compile(r"\b(?:next|upcoming|schedule|schedules|when)\b")
_GAMES = re.compile(r"\b(?:game|games|match|matches|meet|meets)\b")
# A question about playing a sport oneself, or a club or intramural team, is not a varsity
# team's schedule: "is there a club volleyball team", "can I try out for soccer".
_NOT_VARSITY = re.compile(r"\b(?:club|clubs|intramural|intramurals|join|try ?out|tryouts?|"
                          r"walk ?on|recreational|pickup|pick up)\b")
_RESULTS = re.compile(r"\b(?:score|scores|result|results|won|win|wins|winning|lose|lost|loss|"
                      r"losses|beat|record|doing|did|last|standings?)\b")
# @ stands for one published name. Only these complete, unqualified requests skip the read: any
# other word ("home", "tonight", "after 7", "mar 13") may hold a condition, which the read picks
# (11-19 plan: "whens the next home mens lax game" listed three away games, because the read was
# skipped and home was never picked).
_PLAIN_LOOKUP = re.compile(
    r"who coaches (?:the )?@|who is (?:the )?@ coach|"
    r"(?:what(?:s| is) (?:the )?)?@ (?:coach|record)|how did (?:the )?@ do this season|"
    r"when is the next @ (?:game|match|meet)|when does @ play next")
# The most games said of one kind (next or last) for one team; in days the student names, more.
MAX_GAMES = 3
MAX_WINDOW_GAMES = 8
# The conditions a team's games are filtered by (conditions.py).
SLOTS = ("days", "part_of_day", "clock", "home_away")
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


def plain_lookup(message: str, teams: Sequence[Mapping[str, Any]]) -> bool:
    """A complete, unqualified lookup by published team name can skip the reader.

    Extra words may add a date or venue filter, or change the subject altogether.
    """
    words = _words(message)
    for team in teams:
        name = _words(str(team.get("name") or ""))
        if name and name in words and _PLAIN_LOOKUP.fullmatch(words.replace(name, "@", 1)):
            return True
    return False


def asked(message: str, picked: AskedTime | None = None) -> list[str]:
    """What a team question asks: its coaches, its next games, its results (in that order). A
    game asked about with no word of results ("when do they play") asks the next ones, as does a
    team named with days, a clock time or home or away picked (`picked`, the read's record:
    "mens soccer this weekend?")."""
    words = _words(message)
    if _NOT_VARSITY.search(words):
        return []
    asks = ["coaches"] if _COACHES.search(words) else []
    results = bool(_RESULTS.search(words))
    conditioned = bool(set(conditions.asked_of(picked)) & set(SLOTS))
    if _NEXT.search(words) or (_GAMES.search(words) and not results) or (
            conditioned and not results and not asks):
        asks.append("next")
    if results:
        asks.append("results")
    return asks


# A schedule row whose "opponent" is a tournament round still to be drawn ("NJAC Tournament
# Quarterfinal", "NJAC Championship Match"), not a team: it is said without "vs".
_ROUND = re.compile(r"\b(?:tournament|championships?|quarterfinals?|semifinals?|finals?|"
                    r"first round|second round|playoffs?)\b", re.IGNORECASE)


def _game_line(game: Mapping[str, Any], played: bool) -> str:
    side = {"away": "at", "home": "vs", "neutral": "vs"}.get(str(game.get("site")), "vs")
    if side == "vs" and _ROUND.search(str(game.get("opponent") or "")):
        side = ""
    when = str(game.get("date_label") or "")
    if not played and game.get("time"):
        when += f", {game['time']}"
    place = ", ".join(p for p in (str(game.get("venue") or ""), str(game.get("location") or ""))
                      if p)
    line = f"{when}: {f'{side} ' if side else ''}{game.get('opponent')}"
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


def _game_at(game: Mapping[str, Any], window: Window) -> datetime | None:
    """When a game starts on the campus clock; None when its time is not listed ("TBA")."""
    day, minutes = _dated(game), asked_time.minutes_of(game.get("time"))
    if day is None or minutes is None:
        return None
    return datetime.combine(day, time(0, 0), tzinfo=window.start.tzinfo) + timedelta(
        minutes=minutes)


def _fits(game: Mapping[str, Any], window: Window) -> bool:
    """Whether a game is on a day asked and within the limits on the time of day. A game with
    no time listed cannot be checked against those: it fits when it is on a day asked."""
    day = _dated(game)
    if day is None:
        return False
    at = _game_at(game, window)
    if at is None:
        return window.on_dates(datetime.combine(day, time(12, 0), tzinfo=window.start.tzinfo))
    return window.holds(at, None)


def _upcoming_line(game: Mapping[str, Any], window: Window | None) -> str:
    line = _game_line(game, False)
    if window is not None and window.hours and _game_at(game, window) is None:
        line += " (no time listed yet, so it may not fit the time asked)"
    return line


def team_text(team: Mapping[str, Any], asks: Sequence[str], today: date,
              schedule_n: int, coaches_n: int | None, site: str = "any",
              window: Window | None = None) -> str | None:
    """What code says of one team: the coaches, the next games or the results asked, each with
    its source's [n]. With `site` home or away (the read's pick, asked_time.py), only those games
    (18b blind run: "when's the next home swim meet?" got only the away meet), and the games
    with no home or away mark said apart; with none left, that none is (a home game is never
    offered for an away question: dev 4c4b7bb). With
    `window` (the days and times the read picked, asked_time.asked_window), the next games are
    those in it, said with the window ("tonight, Tuesday, November 3, after 7 PM"); with none in
    it, that none is, then the next game after it (11-19 plan: "is there a womens bball game
    tonight" listed the next three games and never said "not tonight"). None when nothing asked
    is in the record."""
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
    every = [g for g in team.get("games") or [] if g.get("opponent")]
    games = [g for g in every if site == "any" or g.get("site") == site]
    which = "" if site == "any" else f"{site} "
    dated = [(d, g) for g in games if (d := _dated(g)) is not None]
    upcoming = [g for d, g in dated if d >= today and not g.get("result")]
    played = [g for d, g in dated if d <= today and g.get("result")]
    ahead = [g for g in every if (d := _dated(g)) is not None and d >= today
             and not g.get("result")]
    season = str(team.get("season") or "")
    its = f"its {season} schedule" if season else "its schedule"
    if "next" in asks:
        if window is not None and upcoming:
            said = asked_time.phrase(window.label, window.start, window.end, window.days_said)
            fits = [g for g in upcoming if _fits(g, window)]
            if fits:
                lines = [_upcoming_line(g, window) for g in fits[:MAX_WINDOW_GAMES]]
                parts.append(f"{name}'s {which}game{'s' if len(fits) > 1 else ''} {said}: "
                             + "; ".join(lines) + f" [{schedule_n}].")
            else:
                # The nearest game that does not fit: tonight's 7 PM game for "before 7
                # tonight", the next one after the day asked for "march 13".
                later = [g for g in upcoming if (_dated(g) or today) >= window.start.date()]
                after = (later or upcoming)[0]
                parts.append(f"{name} has no {which}game listed {said} [{schedule_n}]. Its next "
                             f"{which}game: {_game_line(after, False)} [{schedule_n}].")
        elif upcoming:
            lines = [_game_line(g, False) for g in upcoming[:MAX_GAMES]]
            parts.append(f"{name}'s next {which}game{'s' if len(lines) > 1 else ''}: "
                         + "; ".join(lines) + f" [{schedule_n}].")
        if not upcoming and (games or which):
            parts.append(f"{name} has no more {which}games on {its} [{schedule_n}].")
        unmarked = [g for g in ahead if site != "any" and not g.get("site")
                    and (window is None or _fits(g, window))]
        if unmarked:
            parts.append("Not marked home or away on the schedule: "
                         + "; ".join(_game_line(g, False) for g in unmarked[:MAX_GAMES])
                         + f" [{schedule_n}].")
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
            parts.append(f"Last {which}game{'s' if len(last) > 1 else ''}: "
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


def answer(message: str, teams: Sequence[Mapping[str, Any]], today: date, site: str = "any",
           picked: AskedTime | None = None, now: datetime | None = None,
           breaks: Sequence[tuple[str, date, date]] = (),
           ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], Receipt] | None:
    """Code's answer to a question naming at most two teams and asking their coaches, next games
    or results: the text, its sources, the team records it was written from, and what it did
    with the conditions the read picked (`picked`; conditions.py): home or away, and for the next
    games the days and times asked (from `now`, `breaks` dating a named break). A condition it
    could not use is named in the text. None for anything else (no team named, nothing of these
    asked, a record with none of it)."""
    named = teams_named(message, teams)
    asks = asked(message, picked)
    if picked is not None and picked.home_away != "any":
        site = picked.home_away
    timed = {"days", "part_of_day", "clock"} & set(conditions.asked_of(picked))
    # The days asked count from the start of the day: a game today with no result yet is still
    # today's, and may be on now (11-19 study, asked at 8 PM: "a bball game after 7 tonight"
    # was told none, then that the next game was tonight at 7:30).
    first = datetime.combine((now.date() if now is not None else today), time(0, 0),
                             tzinfo=now.tzinfo if now is not None else None)
    window = (asked_time.asked_window(picked, first, breaks)
              if timed and "next" in asks else None)
    if not named and teams and asks == ["next"] and site != "any":
        # Home or away is only on the team schedules, never on the calendar: "any home games
        # wed or thurs" names no sport, so every team's games are held to it (11-19 study:
        # the calendar listed 20 events and said it couldn't tell home from away).
        return _every_team(teams, today, site, window, picked)
    if not named or len(named) > 2 or not asks:
        return None
    used = (["home_away"] if site != "any" else []) + (sorted(timed) if window else [])
    receipt = conditions.receipt(picked, used, window, "the team schedules", SLOTS)
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
        said = team_text(team, asks, today, schedule_n, coaches_n, site, window)
        if said:
            texts.append(said)
    if not texts:
        return None
    if receipt.said:
        texts.append(receipt.said)
    return "\n\n".join(texts), sources, [dict(t) for t in named], receipt


def _every_team(teams: Sequence[Mapping[str, Any]], today: date, site: str,
                window: Window | None, picked: AskedTime | None,
                ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], Receipt] | None:
    """Every team's home (or away) games in the days and times asked, in date order, each with
    its team and its schedule's [n]; with none, that none is, then the next one after the days
    asked. None when no team lists a game."""
    which, an = f"{site} ", "an" if site == "away" else "a"
    games: list[tuple[date, int, Mapping[str, Any], Mapping[str, Any]]] = []
    unmarked: list[tuple[date, int, Mapping[str, Any], Mapping[str, Any]]] = []
    for team in teams:
        for game in team.get("games") or []:
            day = _dated(game)
            if day is None or day < today or game.get("result") or not game.get("opponent"):
                continue
            at = asked_time.minutes_of(game.get("time"))
            row = (day, at if at is not None else 1440, team, game)
            if game.get("site") == site:
                games.append(row)
            elif not game.get("site"):
                unmarked.append(row)
    if not games and not unmarked:
        return None
    games.sort(key=lambda r: (r[0], r[1]))
    unmarked.sort(key=lambda r: (r[0], r[1]))
    sources: list[dict[str, Any]] = []
    cited: list[Mapping[str, Any]] = []

    def n_of(team: Mapping[str, Any]) -> int:
        if team not in cited:
            cited.append(team)
            sources.append({"n": len(sources) + 1, "url": team.get("source_url"),
                            "title": f"{team.get('name')} schedule (ramapoathletics.com)"})
        return cited.index(team) + 1

    def line(row: tuple[date, int, Mapping[str, Any], Mapping[str, Any]]) -> str:
        return f"{row[2].get('name')}, {_upcoming_line(row[3], window)} [{n_of(row[2])}]"

    parts: list[str] = []
    if window is not None:
        said = asked_time.phrase(window.label, window.start, window.end, window.days_said)
        fits = [r for r in games if _fits(r[3], window)]
        if fits:
            parts.append(f"{which.title()}games on the team schedules {said}: "
                         + "; ".join(line(r) for r in fits[:MAX_WINDOW_GAMES]) + ".")
        else:
            parts.append(f"None of the team schedules lists {an} {which}game {said}.")
            later = [r for r in games if r[0] >= window.start.date()]
            if later:
                parts.append(f"The next {which}game: {line(later[0])}.")
        unmarked = [r for r in unmarked if _fits(r[3], window)]
    elif games:
        parts.append(f"The next {which}games on the team schedules: "
                     + "; ".join(line(r) for r in games[:MAX_GAMES]) + ".")
    else:
        parts.append(f"None of the team schedules lists an upcoming {which}game.")
    if unmarked:
        parts.append("Not marked home or away on the schedule: "
                     + "; ".join(line(r) for r in unmarked[:MAX_GAMES]) + ".")
    if not parts:
        return None
    timed = {"days", "part_of_day", "clock"} & set(conditions.asked_of(picked))
    used = ["home_away", *(sorted(timed) if window else [])]
    receipt = conditions.receipt(picked, used, window, "the team schedules", SLOTS)
    if receipt.said:
        parts.append(receipt.said)
    return " ".join(parts), sources, [dict(t) for t in cited], receipt
