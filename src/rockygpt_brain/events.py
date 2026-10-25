"""Campus events and clubs: which calendar events a question asks about, and which clubs.

Code reads the question's time words into a window on the campus clock ("tonight", "this
weekend", "next friday", "oct 30"; the next two weeks when none), keeps the calendar events whose
date (in their identity's name) falls in it, and ranks them by the question's other words against
each event's title, tags, organizer and location. A club is named by its directory name. The
events and clubs are then read through the shared reader; the model only words the reply.
"""

import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any

from rockygpt_brain.retrieval.entity_facts import discovery_tokens

CALENDAR_URL = "https://archway.ramapo.edu/home/events/"
MAX_EVENTS = 15
DEFAULT_DAYS = 14
_EVENT_WORDS = re.compile(
    r"\b(?:events?|happening|going on|games?|match(?:es)?|"
    r"shows?|concerts?|performances?|plays?|recitals?|fairs?|workshops?|speakers?|lectures?|"
    r"free food|parties|party|things to do|anything to do|calendar|homecoming|tournaments?|"
    r"screenings?|exhibits?|exhibitions?|movie nights?|game nights?|trivia|bingo|karaoke|"
    r"open mics?|festivals?|celebrations?|info(?:rmation)? sessions?|socials?|mixers?|"
    r"fundraisers?|ceremon(?:y|ies)|activities|coming up|upcoming)\b")
# A time alone asks for the calendar ("anything tonight?"), but not beside a subject code answers
# from its own records: "dinner at the Birch tonight" is the menu, "the last shuttle tonight" the
# timetable, "is the Library open tonight" its hours.
_WHEN_WORDS = re.compile(r"\b(?:tonight|this weekend|next weekend|tmrw|tmr)\b")
_OTHER_SUBJECT = re.compile(
    r"\b(?:menus?|eat|food|meals?|breakfast|brunch|lunch|dinner|dining|shuttles?|bus|buses|"
    r"train|hours|open|opens|close|closes|closing|closed)\b")
_CLUB_WORDS = re.compile(
    r"\b(?:clubs?|organi[sz]ations?|orgs?|greek|sororit(?:y|ies)|fraternit(?:y|ies)|"
    r"student groups?|honor societ(?:y|ies))\b")
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH_DAY = re.compile(r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b")
_NUMERIC_DAY = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b")
_EVENT_DATE = re.compile(r"\((\d{4})-(\d{2})-(\d{2})\)\s*$")
# Words of a question that say when or what kind of thing, not what the event is about.
_NOT_TOPIC = discovery_tokens(
    "what whats is are there any events event happening going on tonight today tomorrow this "
    "next weekend week month on campus at ramapo college upcoming coming up soon when where "
    "who can i we go to do things anything fun free for me my friends students student "
    "monday tuesday wednesday thursday friday saturday sunday morning afternoon evening night "
    "time times date dates schedule calendar anywhere anything somewhere around usually ppl "
    "people gonna get got early many much how should would could will want need know see find "
    "some good like also near tmrw tmr whos host hosting hosted packed crowded busy worth "
    "couple attend attending have has had does did are was were")


def asks_events(message: str) -> bool:
    text = message.casefold()
    return bool(_EVENT_WORDS.search(text)
                or (_WHEN_WORDS.search(text) and not _OTHER_SUBJECT.search(text)))


def asks_clubs(message: str) -> bool:
    return bool(_CLUB_WORDS.search(message.casefold()))


def _end(day: date, now: datetime) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=now.tzinfo)


def _start(day: date, now: datetime) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=now.tzinfo)


_PART_OF_DAY = ((re.compile(r"\b(?:night|evening)\b"), 17, 24, "evening"),
                (re.compile(r"\bafternoon\b"), 12, 17, "afternoon"),
                (re.compile(r"\bmorning\b"), 0, 12, "morning"))


def window(message: str, now: datetime) -> tuple[datetime, datetime, str]:
    """The span of campus time a question asks about, and its name; the next two weeks when the
    question names no time. A part of the day ("friday night") narrows a one-day window."""
    start, end, label = _days(message, now)
    if start.date() == end.date() and label != "tonight":
        for words, first, last, name in _PART_OF_DAY:
            if words.search(message.casefold()):
                day = start.date()
                start = max(start, datetime.combine(day, time(first, 0), tzinfo=now.tzinfo))
                end = (datetime.combine(day, time(last, 0), tzinfo=now.tzinfo)
                       if last < 24 else end)
                return start, end, f"{label}, {name}"
    return start, end, label


def _days(message: str, now: datetime) -> tuple[datetime, datetime, str]:
    text = message.casefold()
    today = now.date()
    if "tonight" in text:
        return max(now, datetime.combine(today, time(17, 0), tzinfo=now.tzinfo)), \
            _end(today, now), "tonight"
    if re.search(r"\b(?:tomorrow|tmrw|tmr|tomorow|tommorow|tommorrow)\b", text):
        day = today + timedelta(days=1)
        return _start(day, now), _end(day, now), "tomorrow"
    if "today" in text:
        return _start(today, now), _end(today, now), "today"
    weekend = re.search(r"\b(this|next)?\s*weekend\b", text)
    if weekend:
        friday = today + timedelta(days=(4 - today.weekday()) % 7)
        if today.weekday() >= 5:  # Saturday or Sunday: this weekend is now.
            friday = today - timedelta(days=today.weekday() - 4)
        if weekend.group(1) == "next":
            friday += timedelta(days=7)
        start = max(now, datetime.combine(friday, time(17, 0), tzinfo=now.tzinfo))
        return start, _end(friday + timedelta(days=2), now), (
            "next weekend" if weekend.group(1) == "next" else "this weekend")
    if "next week" in text:
        monday = today + timedelta(days=7 - today.weekday())
        return _start(monday, now), _end(monday + timedelta(days=6), now), "next week"
    if "this week" in text:
        # On a Sunday the week ahead is meant, not the hours left in it.
        last = today + timedelta(days=6 - today.weekday() + (7 if today.weekday() == 6 else 0))
        return now, _end(last, now), "this week"
    for n, name in enumerate(_DAYS):
        if re.search(rf"\b{name}s?\b", text):
            ahead = (n - today.weekday()) % 7
            if re.search(rf"\bnext {name}", text) and ahead == 0:
                ahead = 7
            day = today + timedelta(days=ahead)
            return _start(day, now), _end(day, now), f"{name.title()}, {day:%B} {day.day}"
    found = _MONTH_DAY.search(text)
    numeric = _NUMERIC_DAY.search(text)
    if found or numeric:
        month = (_MONTHS.index(found.group(1)) + 1) if found else int(numeric.group(1))  # type: ignore[union-attr]
        dom = int(found.group(2)) if found else int(numeric.group(2))  # type: ignore[union-attr]
        try:
            day = date(today.year, month, dom)
            if day < today:
                day = date(today.year + 1, month, dom)
            return _start(day, now), _end(day, now), f"{day:%A, %B} {day.day}"
        except ValueError:
            pass
    if "this month" in text:
        last = (date(today.year + (today.month == 12), today.month % 12 + 1, 1)
                - timedelta(days=1))
        return now, _end(last, now), "this month"
    return now, _end(today + timedelta(days=DEFAULT_DAYS), now), f"the next {DEFAULT_DAYS} days"


def events_in(events: Sequence[dict[str, Any]], start: datetime,
              end: datetime) -> list[dict[str, Any]]:
    """The calendar event identities dated within the window (their names end in the date)."""
    found = []
    for event in events:
        dated = _EVENT_DATE.search(str(event["name"]))
        if dated and start.date() <= date(*map(int, dated.groups())) <= end.date():
            found.append(event)
    return found


def _stems(text: str) -> set[str]:
    """Words with a plural s dropped ("nights" is night, "games" is game)."""
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            for w in discovery_tokens(text)}


def topic_words(message: str) -> set[str]:
    """The words of a question that say what an event or club is about."""
    return _stems(message) - _stems(" ".join(_NOT_TOPIC))


def asked_topic(message: str) -> set[str]:
    """The topic words events are matched on; a food question's food words are matched on what
    each listing says instead (food_mention)."""
    food = _FOOD_ASK.findall(message.casefold())
    return (topic_words(message) - (_stems(" ".join(food)) if food else set())
            - _stems(" ".join(_GENERIC_CLUB)))


def _about(value: dict[str, Any]) -> set[str]:
    return _stems(" ".join([str(value.get("title") or ""), " ".join(value.get("tags") or []),
                            str(value.get("organizer") or ""), str(value.get("location") or ""),
                            str(value.get("description") or "")]))


# An event is about the topic asked when it holds words carrying at least this share of the
# question's topic, each word weighted by how rare it is on the calendar: "trivia nights" is a
# trivia event, but "basketball game" is not any game when no listing says basketball.
TOPIC_SHARE = 0.5


def alternatives(message: str, words: set[str]) -> list[set[str]]:
    """The topic words of each alternative a question offers ("movie or trivia nights",
    "theater, dance"): the whole topic, and each part split at "or", commas and slashes that
    keeps a topic word."""
    parts = [words & topic_words(part) for part in re.split(r"\bor\b|,|/", message.casefold())]
    return [words, *[p for p in parts if p and p != words]]


def topic_share(values: Sequence[dict[str, Any]], words: set[str],
                corpus: Sequence[dict[str, Any]]) -> dict[int, float]:
    """Each event's share of the topic words (by id of its value), weighted by rarity across
    the corpus (the calendar's upcoming events)."""
    abouts = [_about(v) for v in corpus]
    weight = {w: math.log((len(abouts) + 1) / (sum(w in a for a in abouts) + 0.5))
              for w in words}
    total = sum(weight.values()) or 1.0
    return {id(v): sum(weight[w] for w in words & _about(v)) / total for v in values}


# An event found by its card's meaning (title, tags, host, place, admission, description). On
# 13 sample questions (10-22) a topic's best event scored 0.47-0.58 ("free food", "volleyball
# games", "halloween", "arts performances"), questions about no event at most 0.43; events
# within EVENT_NEAR of the window's best count as matching.
EVENT_FLOOR, EVENT_NEAR = 0.44, 0.06


def _starts(value: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value.get("starts_at")))
    except ValueError:
        return None


def in_window(values: Sequence[dict[str, Any]], start: datetime,
              end: datetime) -> list[dict[str, Any]]:
    """The events that start in the window (or began up to three hours before it, so are
    likely still on), in time order."""
    timed = [v for v in values if (at := _starts(v)) is not None
             and start - timedelta(hours=3) <= at <= end]
    return sorted(timed, key=lambda v: _starts(v) or start)


def rank_events(values: Sequence[dict[str, Any]], message: str, start: datetime,
                end: datetime, scores: Mapping[str, float] | None = None,
                corpus: Sequence[dict[str, Any]] | None = None, by_share: bool = False,
                ) -> tuple[list[dict[str, Any]], bool | None]:
    """The events in the window that have not ended, in time order, at most MAX_EVENTS: those
    sharing the question's topic words with their title, tags, organizer, location or
    description, and those whose card's meaning (scores, by each value's "_id") is near the
    window's best above EVENT_FLOOR, when any are. Also whether the topic matched: None with no
    topic words, False when none matched (the whole window is then given, and the reply says
    the calendar lists nothing for the topic)."""
    timed = in_window(values, start, end)
    food = asks_food(message)
    words = asked_topic(message)
    if words or food:
        # Alternatives ("theater or dance performances") each match on their own.
        share: dict[int, float] = {}
        for alternative in alternatives(message, words):
            for key, part in topic_share(timed, alternative, corpus or values).items():
                share[key] = max(share.get(key, 0.0), part)
        meaning = {id(v): (scores or {}).get(str(v.get("_id")), 0.0) for v in timed}
        best = max(meaning.values(), default=0.0)
        matching = []
        for value in timed:
            # How each event matched, for the reply to say: its listing names the topic, it
            # mentions food, or it is only near the topic in meaning.
            by = ("words" if share.get(id(value), 0.0) >= TOPIC_SHARE
                  else "food" if food and food_mention(value)
                  else "meaning" if meaning[id(value)] >= max(EVENT_FLOOR, best - EVENT_NEAR)
                  else None)
            if by:
                matching.append({**value, "_matched_by": by})
        if matching:
            if by_share:  # Only the most on-topic, by time.
                top = max(round(share.get(id(v), 0.0), 2) for v in matching)
                matching = [v for v in matching if round(share.get(id(v), 0.0), 2) == top]
            return matching[:MAX_EVENTS], True
        return timed[:MAX_EVENTS], False
    return timed[:MAX_EVENTS], None


# Food a listing mentions ("pizza", "snacks", "delicious food"): a question about food at events
# is answered from what each listing says, not from its title alone.
_FOOD = re.compile(
    r"\b(?:food|pizza|snacks?|refreshments?|cookies?|treats?|candy|donuts?|bagels?|desserts?|"
    r"lunch|dinner|breakfast|brunch|tacos?|cider|cocoa|hot chocolate|ice cream|s.mores|wings|"
    r"catered|meals?|drinks?|coffee|popcorn|cupcakes?|cakes?|dumplings?|mocktails?)\b", re.I)
_FOOD_ASK = re.compile(r"\b(?:food|eat|eating|snacks?|pizza|hungry|refreshments?|treats?|"
                       r"candy|cookies?|meals?)\b")


def asks_food(message: str) -> bool:
    return bool(_FOOD_ASK.search(message.casefold()))


def food_mention(value: dict[str, Any]) -> str | None:
    """The first sentence of an event's listing (title, description, tags) that mentions food,
    as written; None when none does."""
    text = "\n".join([str(value.get("title") or ""), str(value.get("description") or ""),
                      ", ".join(value.get("tags") or [])])
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        if _FOOD.search(sentence):
            return " ".join(sentence.split())[:220]
    return None


def event_line(value: dict[str, Any]) -> dict[str, Any]:
    """What the reply is given for one event, as published, with the start of its
    description."""
    when = " ".join(part for part in (str(value.get("date") or ""),
                                      str(value.get("start_time") or ""),
                                      f"to {value['end_time']}" if value.get("end_time") else "")
                    if part)
    return {"event": value.get("title"), "when": when, "location": value.get("location"),
            "organizer": value.get("organizer"), "tags": value.get("tags") or [],
            **({"tickets": value["ticket_status"]} if value.get("ticket_status") else {}),
            **({"about": " ".join(str(value["description"]).split())[:DESCRIPTION_CHARS]}
               if value.get("description") else {}),
            "url": value.get("url")}


DESCRIPTION_CHARS = 240


_GENERIC_CLUB = discovery_tokens("club clubs society association organization union team group "
                                 "student students council chapter")


def find_clubs(clubs: Sequence[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """The clubs a message names by every word of their directory name (longest first); a name
    of only generic words ("Club") names nothing."""
    words = discovery_tokens(message)
    named = [(club, core) for club in clubs
             if (core := discovery_tokens(str(club["name"]))) and core <= words
             and not core <= _NOT_TOPIC | _GENERIC_CLUB]
    named.sort(key=lambda pair: -len(pair[1]))
    return [club for club, _ in named][:6]


# A club found by its card's meaning (name and category). On 10 sample questions (10-22) club
# questions scored 0.45-0.67 against their best club and others at most 0.31.
CLUB_FLOOR, CLUB_NEAR, MAX_CLUBS = 0.44, 0.08, 8


def clubs_by_meaning(scores: Sequence[tuple[str, float]],
                     names: dict[str, str]) -> list[dict[str, Any]]:
    """The clubs whose cards best match a question: above the floor and near the best, at most
    MAX_CLUBS ("cultural clubs" lists several, "is there an anime club" the nearest few)."""
    if not scores or scores[0][1] < CLUB_FLOOR:
        return []
    return [{"id": entity_id, "name": names[entity_id]} for entity_id, score in scores
            if score >= max(CLUB_FLOOR, scores[0][1] - CLUB_NEAR) and entity_id in names
            ][:MAX_CLUBS]


MAX_CATEGORY_CLUBS = 25
_GENERIC_CATEGORY = {"student organization", "department", "office sponsored organization"}


def club_category(found: Sequence[dict[str, Any]],
                  directory: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every club of the category most of the clubs found share, when it is a specific one
    ("Greek Life", "Honor Society": not "Student Organization"): a question about a kind of
    club wants the kind, not the few nearest. Empty when no such category leads."""
    def head(entry: dict[str, Any]) -> str:
        return str(entry.get("category") or "").split(" - ")[0].strip().casefold()
    heads = [head(e) for e in found if head(e)]
    if len(heads) < 3:
        return []
    top = max(set(heads), key=heads.count)
    if top in _GENERIC_CATEGORY or heads.count(top) * 2 < len(found):
        return []
    return [e for e in directory if head(e) == top]


def interests(message: str) -> list[str]:
    """The interests a club question lists, each on its own ("anime and community service"):
    the question's clauses split at "and", "or" and commas, keeping those with a topic word."""
    text = re.sub(r"\b(?:which|what|club|clubs|should|could|would|join|i|like|love|into|"
                  r"interested in|am|im|i'm|do|you|have|are|there|any)\b", " ", message.casefold())
    bits = [b.strip(" ?.!") for b in re.split(r",|\band\b|\bor\b|&|/", text)]
    return [b for b in bits if topic_words(b)][:4]



def _day(moment: datetime) -> str:
    return f"{moment:%A, %B} {moment.day}"


def computed_answers(label: str, start: datetime, end: datetime, in_window: int,
                     events: Sequence[dict[str, Any]], matched: bool | None, words: set[str],
                     food: bool, outside: Sequence[dict[str, Any]] = (),
                     calendar_n: int | None = None) -> list[dict[str, Any]]:
    """What code works out from the calendar for the reply to state as it is: the window's
    dates; how many events it holds, how many are about the topic asked or that none is (and
    the topic's events outside the window); for a food question, which listings mention food
    and what they say. `events` are the window's lines given (with "n" and the published
    value under "_value"); `outside` the topic's lines after the window; `calendar_n` the
    calendar page's number, for the facts about the whole window."""
    span = (_day(start) if start.date() == end.date()
            else f"{_day(start)} to {_day(end)}")
    where = f"on {span}" if start.date() == end.date() else f"from {span}"
    topic = ", ".join(sorted(w for w in words if len(w) > 2))
    facts: list[dict[str, Any]] = [{"fact": f"The window asked ({label}) is {span}.",
                                    "n": calendar_n}]
    shown = len(events)
    if matched is None:
        facts.append({"fact": f"The calendar lists {in_window} event"
                              f"{'s' if in_window != 1 else ''} {where}"
                              + (f"; the first {shown} in time order are given."
                                 if shown < in_window else "."), "n": calendar_n})
    elif matched:
        named = [line for line in events if (line.get("_value") or {}).get("_matched_by")
                 in ("words", "food")]
        near = [line for line in events if (line.get("_value") or {}).get("_matched_by")
                == "meaning"]
        about = (f"whose listing names {topic}" + (" or mentions food" if food else "")
                 if topic else "whose listing mentions food")
        facts.append({"fact": f"The calendar lists {len(named)} event"
                              f"{'s' if len(named) != 1 else ''} {about} {where} (of "
                              f"{in_window} events then).", "n": calendar_n})
        if near:
            facts.append({"fact": f"Only near {topic or 'the question'} in meaning, not named "
                                  "by their listings: "
                                  + "; ".join(f"{line['event']} [{line['n']}]" for line in near)
                                  + ".", "n": near[0]["n"]})
    else:
        about = f"about {topic}" if topic else "whose listing mentions food"
        facts.append({"fact": f"The calendar lists no event {about} {where}; it lists "
                              f"{in_window} other event{'s' if in_window != 1 else ''} then.",
                      "n": calendar_n})
        if outside:
            facts.append({"fact": f"Outside {span}, the calendar's next events {about}, by date: "
                                  + "; ".join(f"{line['event']} ({line['when']}) [{line['n']}]"
                                              for line in outside) + ".",
                          "n": outside[0]["n"]})
        else:
            facts.append({"fact": f"No upcoming event on the whole calendar is {about}.",
                          "n": calendar_n})
    if food:
        with_food = [(line, said) for line in events
                     if (said := food_mention(line.get("_value") or {}))]
        for line, said in with_food:
            facts.append({"fact": f"{line['event']} ({line['when']}) mentions food in its "
                                  f'listing: "{said}"', "n": line["n"]})
        if not with_food and topic:
            facts.append({"fact": f"No event listing {where} mentions food.", "n": calendar_n})
    return facts


_CLUB_TIME_ASK = re.compile(r"\b(?:when|meet|meets|meeting|meetings|perform\w*|events?|"
                            r"schedule|next|upcoming|coming up)\b")


def asks_club_events(message: str) -> bool:
    return bool(_CLUB_TIME_ASK.search(message.casefold()))


def club_answers(clubs: Sequence[dict[str, Any]], message: str, by_name: bool,
                 directory_size: int) -> list[dict[str, Any]]:
    """What code works out about the clubs a question asks for: each club found by its name
    as the directory lists it; for clubs found only by meaning, whether any has a word the
    question asked for in its name (else they are only the nearest); or that the directory lists
    no club for what was asked."""
    words = topic_words(message) - _GENERIC_CLUB
    topic = ", ".join(sorted(w for w in words if len(w) > 2))
    if not clubs:
        return [{"fact": f"The club directory ({directory_size} clubs and organizations) lists "
                         f"no club for {topic or 'what was asked'}."}]
    facts: list[dict[str, Any]] = []
    if not by_name:
        named = [c for c in clubs if _stems(" ".join(words)) & _stems(str(c.get("name") or ""))]
        if not named:
            facts.append({"fact": f"No club in the directory has {topic or 'the words asked'} in "
                                  "its name; the clubs given are only the nearest by meaning."})
    if len(clubs) > MAX_CLUB_LINES:
        categories = sorted({str(c.get("category") or "") for c in clubs})
        facts.append({"fact": f"{len(clubs)} clubs are given, from the directory's "
                              f"{'; '.join(categories)} listing.", "n": clubs[0]["n"]})
        return facts
    for club in clubs:
        facts.append({"fact": f"The club directory lists {club.get('name')} "
                              f"({club.get('category')}): {club.get('url')}.", "n": club["n"]})
    return facts


MAX_CLUB_LINES = 6
MAX_OUTSIDE = 3


MAX_WORD_CLUBS = 12


def clubs_by_words(directory: Sequence[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """The directory clubs whose name has a word the question asks about ("arts" for an art
    club question, "commuter" for Commuter Affairs), at most MAX_WORD_CLUBS."""
    words = _stems(" ".join(topic_words(message) - _GENERIC_CLUB))
    words = {w for w in words if len(w) > 2}
    return [c for c in directory if words & _stems(str(c.get("name") or ""))][:MAX_WORD_CLUBS]


def clubs_on_pages(directory: Sequence[dict[str, Any]], text: str,
                   ordinary: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The directory clubs a page given names by their whole directory name ("Black Student
    Union", "Naach"); a one-word name only when it is not an ordinary word of the pages."""
    folded = " ".join(re.findall(r"[^\W_]+", text.casefold()))
    found = []
    for club in directory:
        words = re.findall(r"[^\W_]+", str(club.get("name") or "").casefold())
        if not words or set(words) <= _GENERIC_CLUB | {"ramapo", "the", "of", "and"}:
            continue
        if len(words) == 1 and (len(words[0]) < 5 or words[0] in ordinary):
            continue
        if re.search(rf"\b{re.escape(' '.join(words))}\b", folded):
            found.append(club)
    return found
