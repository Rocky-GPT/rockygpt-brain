"""Campus events and clubs: which calendar events a question asks about, and which clubs.

Code reads the question's time words into a window on the campus clock ("tonight", "this
weekend", "next friday", "oct 30"; the next two weeks when none), keeps the calendar events whose
date (in their identity's name) falls in it, and ranks them by the question's other words against
each event's title, tags, organizer and location. A club is named by its directory name. The
events and clubs are then read through the shared reader. A question asking only what is on in a
window is answered in code's own words (listing_text); otherwise the model only words the reply.
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from datetime import date, datetime, time, timedelta
from typing import Any

from rockygpt_brain.kinds import KINDS, with_included
from rockygpt_brain.plain_writer import literal
from rockygpt_brain.retrieval.entity_facts import discovery_tokens
from rockygpt_brain.retrieval.meaning import standout_bar

CALENDAR_URL = "https://archway.ramapo.edu/home/events/"
MAX_EVENTS = 15
DEFAULT_DAYS = 14
DEFAULT_LABEL = f"the next {DEFAULT_DAYS} days"
_EVENT_WORDS = re.compile(
    r"\b(?:events?|happening|going on|games?|match(?:es)?|"
    r"shows?|concerts?|performances?|plays?|recitals?|fairs?|workshops?|speakers?|lectures?|"
    r"free food|parties|party|things to do|anything to do|calendar|homecoming|tournaments?|"
    r"screenings?|exhibits?|exhibitions?|movie nights?|game nights?|trivia|bingo|karaoke|"
    r"open mics?|festivals?|celebrations?|info(?:rmation)? sessions?|socials|mixers?|"
    r"(?:a|an|any|ice[- ]cream|welcome|pizza|holiday)\s+social\b(?!\s+(?:sciences?|work))|"
    r"fundraisers?|ceremon(?:y|ies)|activities|coming up|upcoming|contests?|competitions?|"
    r"costume|hunts?|5ks?|races?|walks? for|charity walks?|haunted|try ?outs?|auditions?)\b")
# Sessions, meetings or trainings a question asks for ("any study sessions", "any book clubs
# meeting", "is there any narcan training") may be the calendar's or an office's service
# (tutoring, counseling, advising, safety trainings), so the calendar is read and the model writes
# from it and the pages; a term ("summer session") or "is school in session" is the academic
# calendar's (11-03 practice: "any study sessions" and "any book clubs meeting" never reached the
# calendar; fix check 11-04: nor did "is there any narcan training on campus").
_SESSIONS_ASKED = re.compile(
    r"\b(?:any|are there|is there an?)\b(?:\s+(?!summer\b|winter\b|spring\b|fall\b|in\b)[\w'’]+)"
    r"{0,3}\s+(?:sessions?|meetings?|meeting|meets?|trainings?)\b")
# A team a student can join ("is there a hockey team", "how do I join the cheer team") is a club
# sports entry in the club directory ("Ramapo College Ice Hockey Club", "RCNJ Cheer"), and its
# games are on the calendar; "the IT team" or "my team" asks about neither (11-03 practice: "is
# there a hockey team" was answered from an admissions page, without the directory's club).
_TEAM_ASKED = re.compile(
    r"^\W*(?:is there|are there|does (?:ramapo|the college|rcnj|the school|campus) have|"
    r"do (?:you|we|they) have)\b.*\bteams?\b"
    r"|\b(?:join|joining|try ?outs?|tryouts?)\b.*\bteams?\b"
    r"|\bteams?\b.*\b(?:try ?outs?|tryouts?)\b")
# A time alone asks for the calendar ("anything tonight?", "any volunteer opportunities this
# month?"), but not beside a subject code answers from its own records: "dinner at the Birch
# tonight" is the menu, "the last shuttle tonight" the timetable, "is the Library open tonight"
# its hours (fix check 11-04: "this month" was no time, so the month's service events were
# never read).
_WHEN_WORDS = re.compile(r"\b(?:tonight|this weekend|next weekend|tmrw|tmr|this week|next week|"
                         r"this month|next month|"
                         r"(?:this|next) (?:monday|tuesday|wednesday|thursday|friday|saturday|"
                         r"sunday))\b")
_SUBJECT_WORDS = (
    "menu menus eat food meal meals breakfast brunch lunch dinner dining shuttle shuttles bus "
    "buses train hours open opens close closes closing closed weather forecast rain raining "
    "rainy snow snowing snowy temperature temperatures").split()
_OTHER_SUBJECT = re.compile(r"\b(?:" + "|".join(_SUBJECT_WORDS) + r")\b")
# A time beside a bill, a deadline or a grade asks when that is ("is my bill due this month",
# "when do grades come out this week"), not what is on.
_DUE_SUBJECT = re.compile(r"\b(?:due|deadlines?|bills?|billing|pay|paid|payments?|tuition|rent|"
                          r"refunds?|fees?|grades?|transcripts?|registration|register)\b")
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
    "couple attend attending join joining interested have has had does did are was were "
    # Words that say how good something is, not what it is (11-03 repeat test: "any cool
    # events this week" was read as events about "cool", and "Clay it Cool" led the reply).
    "cool interesting great awesome best exciting amazing nice neat chill dope epic fave "
    "favorite favourite enjoyable entertaining "
    # Function words, which never say what an event is about ("is any of it free").
    "a an the and or but nor so yet if then than that this these those it its im you your "
    "he she they them their we us our of in at by with from into onto about over under out "
    "off up as be been being am do doing done here there which whom whose why just only very "
    "really all each every both either neither no not too before after during until while")


# "What shows up on my transcript" and "does it show up" ask about no show, and neither does "my
# portal shows an immunization hold" (fix check 11-04: an immunization question began with "the
# calendar lists no event in the next 14 days").
_NOT_EVENT = re.compile(r"\bshows? up\b|\bshowing up\b|\bno[- ]shows?\b|\bshows\s+(?:a|an|the|my|"
                        r"me|it|him|her|them|how|what|which|no|as)\b")
# An occasion ("Black Solidarity Week", "Pride Month", "Veterans Day") asked about by what is on
# for it ("anything for", "anything on campus for", "what's planned for", "how is ...
# celebrated") is the calendar's: its events are the answer (11-03 practice: "anything for black
# solidarity week" never reached the calendar, and the reply missed its two events; fix check
# 11-04: nor did "is there anything on campus for veterans day"). The occasion is named by at
# least one word that is not a time ("this week", "a week", "the first day").
_OCCASION = (
    re.compile(r"\b(?:anything|something|planned|scheduled|do|doing)\s+(?:(?:happening|going on|"
               r"special|planned|scheduled)\s+)?(?:(?:on campus|at ramapo|at school|here)\s+)?"
               r"(?:for|during|around)\s+((?:[\w'’-]+\s+){1,4}?)"
               r"(?:week|month|weekend|day)\b"),
    re.compile(r"\b(?:celebrat\w*|observ\w*|commemorat\w*|recogni[sz]\w*)\s+((?:[\w'’-]+\s+)"
               r"{1,4}?)(?:week|month|weekend|day)\b"),
    re.compile(r"\b((?:[\w'’-]+\s+){1,4}?)(?:week|month|weekend|day)\s+(?:is\s+|being\s+|be\s+)?"
               r"(?:celebrat|observ|commemorat|recogni[sz])\w*"))
_NOT_OCCASION = frozenset("a an one two three this next last every each per that the whole "
                          "entire same following previous first second third final of how is "
                          "are".split())


def asks_occasion(message: str) -> bool:
    """Whether a question asks what is on for a named week, month, weekend or day (_OCCASION)."""
    text = message.casefold()
    return any(set(found.group(1).split()) - _NOT_OCCASION
               for pattern in _OCCASION for found in pattern.finditer(text))


def names_events(message: str) -> bool:
    """Whether a question names events themselves ("games", "shows", "coming up", what is on for
    an occasion), not only a time: "what's the weather this weekend" names a time and no event
    (11-03 practice: read as a calendar question, it listed the weekend's events)."""
    text = _NOT_EVENT.sub(" ", message.casefold())
    return bool(_EVENT_WORDS.search(text) or _SESSIONS_ASKED.search(text)
                or asks_occasion(text))


def asks_sessions(message: str) -> bool:
    return bool(_SESSIONS_ASKED.search(message.casefold()))


def asks_team(message: str) -> bool:
    return bool(_TEAM_ASKED.search(message.casefold()))


def asks_events(message: str) -> bool:
    text = _NOT_EVENT.sub(" ", message.casefold())
    # "Is there a chess club or a board game club?" asks whether clubs exist: its "game" is the
    # club's, and no event is asked of, unless it asks when or for events (11-03 practice: the
    # reply led with three unrelated events). A team's games are what it does, so they are read.
    if asks_club_exists(message) and not asks_club_events(message) \
            and not _WHEN_WORDS.search(text) and not asks_team(message):
        return False
    return bool(_EVENT_WORDS.search(text) or _SESSIONS_ASKED.search(text) or asks_team(message)
                or asks_occasion(text)
                or (_WHEN_WORDS.search(text) and not _OTHER_SUBJECT.search(text)
                    and not _DUE_SUBJECT.search(text)))


# Starting a club ("how do I start my own club", "can we form a new organization") asks how a
# club is made, which the involvement pages say, not for a club the directory lists (fix check
# 11-04: the reply added "the club directory lists no club named 'start my own'").
_NEW_CLUB = re.compile(
    r"\b(?:start|starting|create|creating|form|forming|found|founding|make|making|register|"
    r"registering|charter|chartering|begin|beginning)\s+(?:(?:a|an|my|our|your|one|some)\s+)?"
    r"(?:(?:own|new|official|student)\s+)*(?:clubs?|organi[sz]ations?|orgs?|student groups?)\b")


def asks_clubs(message: str) -> bool:
    text = message.casefold()
    return bool(_CLUB_WORDS.search(_NEW_CLUB.sub(" ", text)) or _TEAM_ASKED.search(text))


# A question asking only what is on in one time window ("what's happening this weekend", "any
# events tonight?") is answered by code, with no model (listing_text). Any other word ("free",
# "parties", "where", "club") asks for more, and the model reads it.
_WINDOW_ONLY = frozenset("""
what whats which is are there any anything something events event happening going on tonight
today tomorrow tmrw tmr this next weekend week month campus at ramapo college upcoming coming up
soon things thing to do fun for me i can we go all the a an of in around here please calendar
list show tell planned scheduled student students morning afternoon evening night
""".split()) | frozenset(_DAYS) | frozenset(day + "s" for day in _DAYS)
_MONTH_WORDS = frozenset("""
jan january feb february mar march apr april may jun june jul july aug august sep sept september
oct october nov november dec december
""".split())
_WINDOW_WORD = re.compile(r"\b(?:tonight|today|tomorrow|tmrw|tmr|weekend|week|month|"
                          + "|".join(_DAYS) + r")s?\b")


def plain_window(message: str, breaks: Sequence[tuple[str, date, date]] = (),
                 today: date | None = None) -> bool:
    """Whether a question asks only what the calendar lists in one time window (one: "today and
    tomorrow" is two, and the model reads it). A month named ("in december") is one, as is a
    break the academic calendar dates (`breaks`, from `today` on: "over thanksgiving break")."""
    text = message.casefold().replace("’", "'").replace("'", "")
    words = re.findall(r"[a-z0-9]+", text)
    dated = bool(_MONTH_DAY.search(text) or _NUMERIC_DAY.search(text))
    month = not dated and month_asked(message) is not None
    named_break = today is not None and break_asked(message, breaks, today) is not None
    times = len(_WINDOW_WORD.findall(text)) + len(_MONTH_DAY.findall(text)) \
        + len(_NUMERIC_DAY.findall(text)) + month + named_break
    if not words or times > 1 or not asks_events(message):
        return False
    said = {w for m in [*(_month_phrases(text) if month else []),
                        *(_break_phrases(text) if named_break else [])]
            for w in re.findall(r"[a-z0-9]+", m.group(0))}
    return all(w in _WINDOW_ONLY or w in said
               or (dated and (w.isdigit() or w in _MONTH_WORDS
                              or re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)", w)))
               for w in words)


def _end(day: date, now: datetime) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=now.tzinfo)


def _start(day: date, now: datetime) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=now.tzinfo)


_PART_OF_DAY = ((re.compile(r"\b(?:night|evening)\b"), 17, 24, "evening"),
                (re.compile(r"\bafternoon\b"), 12, 17, "afternoon"),
                (re.compile(r"\bmorning\b"), 0, 12, "morning"))


# A month asked by its name ("what's happening in december"): that month (fix check 11-04: read
# as the rest of the semester, it showed the first 15 events, all in November). "May", "March"
# and short forms ("dec") name a month only after a word that says when ("in may", "this march").
_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december")
_MONTH_ASKED = re.compile(
    r"\b(?:(in|during|for|of|this|next|early|late|mid|throughout|all|whole|rest of)\s+)?"
    r"(?:the\s+month\s+of\s+)?(january|february|march|april|may|june|july|august|september|"
    r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\b\.?")
_PLAIN_MONTHS = frozenset(_MONTH_NAMES) - {"may", "march"}


def _month_phrases(text: str) -> list[re.Match[str]]:
    return [m for m in _MONTH_ASKED.finditer(text) if m.group(1) or m.group(2) in _PLAIN_MONTHS]


def month_asked(message: str) -> int | None:
    """The one month a question names without a day, as its number, or None."""
    found = {next(k for k, name in enumerate(_MONTH_NAMES, start=1)
                  if name.startswith(m.group(2)[:3]))
             for m in _month_phrases(message.casefold())}
    return found.pop() if len(found) == 1 else None


# A break named ("over thanksgiving break", "during spring break"): its days on the academic
# calendar (fix check 11-04: "anything going on over thanksgiving break" was read as the rest of
# the semester, and its "break" matched a "Spring Break Info Session"). A season names a break
# only beside "break" ("spring" alone is the term); Thanksgiving also after a word that says
# when ("over thanksgiving"), never alone ("a thanksgiving dinner" is an event, held before it).
_BREAK_ASKED = re.compile(
    r"\b(?:(before|after|until|till|over|during|on)\s+(?:the\s+)?)?"
    r"(thanksgiving|spring|winter|fall|summer)(\s+(?:break|recess|vacation|holidays?|weekend))?\b")


def _break_phrases(text: str) -> list[re.Match[str]]:
    found = []
    for m in _BREAK_ASKED.finditer(text):
        tail = (m.group(3) or "").strip()
        if (tail and (m.group(2) == "thanksgiving" or tail != "weekend")) or (
                m.group(2) == "thanksgiving" and m.group(1)):
            found.append(m)
    return found


def break_asked(message: str, breaks: Sequence[tuple[str, date, date]],
                today: date) -> tuple[str, date, date, str] | None:
    """The break a question names, from the academic calendar's breaks (each (name, first day,
    last day)): the next one by that name that has not ended, with what the question asks of it:
    "before" (until it begins), "after" or "" (its own days)."""
    for m in _break_phrases(message.casefold()):
        for name, first, last in sorted(breaks, key=lambda b: b[1]):
            if m.group(2) in name.casefold() and last >= today:
                when = {"until": "before", "till": "before"}.get(m.group(1) or "", m.group(1) or "")
                return name, first, last, when if when in ("before", "after") else ""
    return None


def window(message: str, now: datetime,
           breaks: Sequence[tuple[str, date, date]] = ()) -> tuple[datetime, datetime, str]:
    """The span of campus time a question asks about, and its name; the next two weeks when the
    question names no time. A part of the day ("friday night") narrows a one-day window. A break
    the question names is read from `breaks`, the academic calendar's (name, first day, last
    day)."""
    start, end, label = _days(message, now, breaks)
    if start.date() == end.date() and label != "tonight":
        for words, first, last, name in _PART_OF_DAY:
            if words.search(message.casefold()):
                day = start.date()
                start = max(start, datetime.combine(day, time(first, 0), tzinfo=now.tzinfo))
                end = (datetime.combine(day, time(last, 0), tzinfo=now.tzinfo)
                       if last < 24 else end)
                return start, end, f"{label}, {name}"
    return start, end, label


def _break_window(named: tuple[str, date, date, str], now: datetime,
                  ) -> tuple[datetime, datetime, str]:
    """A named break's window: its own days ("over Thanksgiving Break"), the days until it
    begins, or the two weeks after it ends."""
    name, first, last, when = named
    said = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if when == "before":
        return now, _end(max(first - timedelta(days=1), now.date()), now), f"before {said}"
    if when == "after":
        begins = last + timedelta(days=1)
        return (max(now, _start(begins, now)),
                _end(begins + timedelta(days=DEFAULT_DAYS - 1), now), f"after {said}")
    return max(now, _start(first, now)), _end(last, now), f"over {said}"


def _days(message: str, now: datetime,
          breaks: Sequence[tuple[str, date, date]] = ()) -> tuple[datetime, datetime, str]:
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
    named_break = break_asked(message, breaks, today)
    if named_break is not None:
        return _break_window(named_break, now)
    for n, name in enumerate(_DAYS):
        if re.search(rf"\b{name}s?\b", text):
            ahead = (n - today.weekday()) % 7
            if re.search(rf"\bnext {name}", text) and ahead == 0:
                ahead = 7
            day = today + timedelta(days=ahead)
            return _start(day, now), _end(day, now), f"{name.title()}, {day:%B} {day.day}"
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
    if "next month" in text:
        first = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
        last = date(first.year + (first.month == 12), first.month % 12 + 1, 1) - timedelta(days=1)
        return _start(first, now), _end(last, now), f"in {first:%B}"
    named_month = month_asked(message)
    if named_month is not None:
        year = today.year if named_month >= today.month else today.year + 1
        first = date(year, named_month, 1)
        last = date(year + (named_month == 12), named_month % 12 + 1, 1) - timedelta(days=1)
        return max(now, _start(first, now)), _end(last, now), f"in {first:%B}"
    # A later part of the term ("finals", "before Thanksgiving break") is past the usual two
    # weeks: the rest of the semester's calendar is read.
    if _LATER_TERM.search(text):
        last = date(today.year, 12, 31) if today.month >= 8 else date(today.year, 6, 30)
        return now, _end(last, now), "the rest of the semester"
    return now, _end(today + timedelta(days=DEFAULT_DAYS), now), DEFAULT_LABEL


_LATER_TERM = re.compile(r"\b(?:finals?|end of (?:the )?(?:semester|term)|thanksgiving|"
                         r"friendsgiving|before (?:the )?break|winter break|holiday|holidays|"
                         r"december|semester)\b")


def events_in(events: Sequence[dict[str, Any]], start: datetime,
              end: datetime) -> list[dict[str, Any]]:
    """The calendar event identities dated within the window (their names end in the date)."""
    found = []
    for event in events:
        dated = _EVENT_DATE.search(str(event["name"]))
        if dated and start.date() <= date(*map(int, dated.groups())) <= end.date():
            found.append(event)
    return found


def _stem(word: str) -> str:
    """A word's plain form, so a question's word and a listing's match however each is written:
    no plural ("fraternities" is fraternity, "classes" is class), no -ing ("studying" is study,
    "gaming" is game, "swimming" is swim), no final e ("dance" and "dancing") and a final y as i
    ("party" and "parties"). Only endings: "cultural" and "culture" stay apart, as do "formal"
    and "form". 11-03 practice: the plural s alone made "fraternitie" of "fraternities", so the
    reply said no club had the word in its name though eight do, and "religious" lost its s."""
    if len(word) <= 3 or not word.isalpha():
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "i"
    elif word.endswith("s") and not (word.endswith("ss")
                                     or (len(word) > 4 and word.endswith(("us", "is")))):
        word = word[:-1]
    if word.endswith("ing") and len(word) > 5 and re.search(r"[aeiouy]", word[:-3]):
        word = word[:-3]
        if word[-1] == word[-2] and word[-1] not in "aeioulsz":
            word = word[:-1]
    if len(word) > 3 and word.endswith("e") and not word.endswith("ee"):
        word = word[:-1]
    if len(word) > 3 and word.endswith("y") and word[-2] not in "aeiou":
        word = word[:-1] + "i"
    return word


def _stems(text: str) -> set[str]:
    """A text's words in their plain form (_stem)."""
    return {_stem(w) for w in discovery_tokens(text)}


def said_words(message: str, words: AbstractSet[str]) -> str:
    """The question's own words for a set of plain forms, in the order asked, for a reply to
    say ("fraternities, sororities", never "fraterniti")."""
    tokens = re.findall(r"[^\W_]+", re.sub(r"(?<=\w)['’]s\b", "", message.casefold()))
    said: list[str] = []
    for token in tokens:
        if _stem(token) in words and token not in said and len(token) > 2:
            said.append(token)
    return ", ".join(said)


def topic_words(message: str) -> set[str]:
    """The words of a question that say what an event or club is about; a word asking for the
    answer (_ASKING) counts only when no other does."""
    words = _stems(_ASKING_PHRASE.sub(" ", message.casefold())) - _stems(" ".join(_NOT_TOPIC))
    return (words - _ASKING) or words


# Words asking for an answer ("list the honor societies", "name some", "which ones exist", "show
# me art events", "tell me about") or pointing back ("how do I join one"), not what it is about
# (11-03: "list the cultural clubs" was read as clubs about "list" and "cultural", so its kind
# was never listed). "Show", "tell" and "give" ask only before "me" or "us": "any art shows" asks
# for shows of art.
_ASKING_PHRASE = re.compile(r"\b(?:show|tell|give)\s+(?:me|us)\b")
_ASKING = _stems("list lists name names exist exists existing one ones")


def asked_topic(message: str) -> set[str]:
    """The topic words events are matched on; a food question's food words are matched on what
    each listing says instead (food_mention), and the words that set a later-term window
    ("finals week", "before thanksgiving") say when, not what. A word saying an event's form
    or how it is liked (_FORM: "sessions", "meeting", "cool") counts only when nothing else is
    asked: "any club meetings" asks for meetings, "any book clubs meeting" for book clubs."""
    text = message.casefold()
    food = _FOOD_ASK.findall(text)
    words = (topic_words(message) - (_stems(" ".join(food)) if food else set())
             - _stems(" ".join(_LATER_TERM.findall(text))) - _stems(" ".join(_GENERIC_CLUB))
             - _stems(" ".join(m.group(0) for m in [*_month_phrases(text),
                                                    *_break_phrases(text)])))
    return (words - _FORM) or words


# Words that say what form an event takes or how a student likes it, not what it is about: "any
# yoga sessions" asks for yoga, "volunteer opportunities" for volunteering, "cool art stuff" for
# art (11-03 practice: "any book clubs meeting" needed "meeting" in a listing, and the silent book
# club, whose listing does not say it, was left out).
_FORM = _stems(
    "workshop workshops session sessions talk talks lecture lectures class classes "
    "meeting meetings meet meets seminar seminars webinar webinars training trainings "
    "panel panels presentation presentations discussion discussions gathering gatherings fair "
    "fairs festival festivals celebration celebrations social socials mixer mixers opportunity "
    "opportunities activity activities option options stuff related type types kind kinds sort "
    "sorts day days series cool interesting exciting awesome cheap")


# A listing titled "A vs. B" is a game, whatever words its description uses.
_VERSUS = re.compile(r"\bvs\b\.?", re.I)


# Groups of students an office or club is for: a question asking only for one group's events
# ("any lgbtq events", "anything going on with the jewish student group") asks for what that
# group's own office or club holds, whatever its listing says (fix check 11-04: Quiet Together,
# held by Women's Center & LGBTQ+ Services, and Kosher Sushi Night, by the Chabad Jewish Student
# Club, were missed because their listings never say "lgbtq" or "jewish"). A subject ("health")
# is not a group: Health Promotion's events are not all health events.
_COMMUNITY = _stems("lgbtq lgbt lgbtqia queer gay lesbian trans transgender pride women womens "
                    "jewish muslim christian catholic hindu sikh buddhist black african "
                    "caribbean latino latina latinx hispanic asian indigenous native veterans "
                    "veteran military international commuters commuter transfer nontraditional "
                    "first-generation parents")


def names_host(organizer: str, words: AbstractSet[str]) -> bool:
    """Whether a question's topic words name an event's host: two of the words its name says what
    it is by, or its one such word ("data science" names the Data Science Club, "board" alone
    not the College Programming Board), or the question names only a group of students its name
    says it is for (_COMMUNITY: "lgbtq" names Women's Center & LGBTQ+ Services)."""
    own = _stems(organizer) - _HOST_FILLER
    return bool(own) and (len(own & words) >= min(2, len(own))
                          or bool(words) and words <= own and words <= _COMMUNITY)


def _about(value: dict[str, Any], words: AbstractSet[str] = frozenset(),
           description: bool = True) -> set[str]:
    """An event's words: its title, tags, place and description (unless left out), and its
    host's name only when the question names the host. A host's name says who runs an event, not
    what it is about (11-03 practice: "a board game club" matched every College Programming
    Board event, whose listings say "Join the College Programming Board"), and a place named for
    its office says where, not what ("service events" matched every event in the room of
    Women's Center & LGBTQ+ Services, 11-03 practice). "Services" in a place's name is an
    office's word."""
    title = str(value.get("title") or "")
    organizer = str(value.get("organizer") or "").strip()
    place = str(value.get("location") or "")
    said = [title, " ".join(value.get("tags") or []),
            str(value.get("description") or "") if description else ""]
    if organizer and not names_host(organizer, words):
        # Its listing names its host in its tags, description and place too, with either
        # apostrophe.
        host = re.compile(_APOSTROPHE.sub("['’]", re.escape(_APOSTROPHE.sub("'", organizer))),
                          re.I)
        said, place, organizer = [host.sub(" ", text) for text in said], host.sub(" ", place), ""
    return _stems(" ".join([*said, organizer, _OFFICE_WORD.sub(" ", place),
                            "game match" if _VERSUS.search(title) else ""]))


_APOSTROPHE = re.compile(r"['’]")
_OFFICE_WORD = re.compile(r"\bservices\b", re.I)


# An event is about the topic asked when it holds words carrying at least this share of the
# question's topic, each word weighted by how rare it is on the calendar: "trivia nights" is a
# trivia event, but "basketball game" is not any game when no listing says basketball.
TOPIC_SHARE = 0.5


def alternatives(message: str, words: set[str]) -> list[set[str]]:
    """The topic words of each alternative a question offers ("movie or trivia nights",
    "theater, dance", "destress events like therapy dogs"): the whole topic, and each part split
    at "or", commas, slashes and the examples "like" or "such as" gives that keeps a topic
    word."""
    parts = [words & topic_words(part)
             for part in re.split(r"\bor\b|,|/|\blike\b|\bsuch as\b", message.casefold())]
    return [words, *[p for p in parts if p and p != words]]


# Each kind's names (kinds.py): its one-word names as written and in their plain form, and its
# phrases word by word ("study abroad", "mental health", "game night"), which name it only where
# the question says them in order.
_KIND_NAMES = [(kind.key, frozenset(w for w in kind.named if " " not in w),
                frozenset(_stem(w) for w in kind.named if " " not in w),
                [tuple(_stem(w) for w in name.split()) for name in kind.named if " " in name])
               for kind in KINDS]


def _said(text: str) -> list[str]:
    """A text's words in order, apostrophes dropped ("women's" is womens)."""
    return re.findall(r"[^\W_]+", _APOSTROPHE.sub("", text.casefold()))


def _kinds_named(said: Sequence[str], core: AbstractSet[str]) -> set[str]:
    """The kinds whose names hold every word of `core` (plain forms), a phrase counting where
    `said` (the words as written) holds it in order. A kind named by a phrase the question says
    outranks one named word by word ("game nights" is games, "home games" sports), and one named
    by the very words said outranks one named by their plain form ("gaming" is games, though its
    plain form is a game's; "games" alone is sports and games)."""
    stems = [_stem(w) for w in said]
    found: set[str] = set()
    phrased: set[str] = set()
    exact: set[str] = set()
    for key, written, single, phrases in _KIND_NAMES:
        present = [p for p in phrases
                   if any(tuple(stems[k:k + len(p)]) == p for k in range(len(stems) - len(p) + 1))]
        by_phrase = set().union(*present)
        if not core <= single | by_phrase:
            continue
        found.add(key)
        if core & by_phrase:
            phrased.add(key)
        if all(word in by_phrase or any(w in written for w, st in zip(said, stems, strict=True)
                                        if st == word) for word in core):
            exact.add(key)
    return phrased or exact or found


def asked_kinds(message: str, words: set[str]) -> tuple[frozenset[str], list[set[str]]]:
    """The kinds of event or club a question asks for (kinds.py), and the alternatives left for
    the listings' own words (alternatives). A kind is asked when every topic word of the question,
    or of each part of it ("art or music", "art and music"), is a name the kind goes by: "any art
    events", "cultural clubs", "volunteer opportunities". A word naming no kind ("black culture",
    "art shows", "painting") leaves its part to the words: kinds are never a member's ("painting"
    is not art and crafts), and a kind beside another word is a narrower thing than the kind."""
    parts = []
    for text in re.split(r"\bor\b|\band\b|&|,|/|\blike\b|\bsuch as\b", message.casefold()):
        if core := words & topic_words(text):
            parts.append((_said(text), core))
    if not parts:
        return frozenset(), alternatives(message, words)
    whole = _kinds_named(_said(message), set().union(*(core for _, core in parts)))
    if whole:
        return frozenset(whole), []
    each = [_kinds_named(said, core) for said, core in parts]
    if not any(each):
        return frozenset(), alternatives(message, words)
    if all(each):
        return frozenset(set().union(*each)), []
    # Some parts name kinds and some do not ("art or black history events"): the others are
    # matched on the listings' words, as are any whole words the kinds leave.
    named = set().union(*(core for (_, core), found in zip(parts, each, strict=True) if found))
    rest = [alt for alt in alternatives(message, words) if not alt <= named]
    rest += [core for (_, core), found in zip(parts, each, strict=True)
             if not found and core not in rest]
    return frozenset(set().union(*each)), rest


def topic_share(values: Sequence[dict[str, Any]], words: set[str],
                corpus: Sequence[dict[str, Any]]) -> dict[int, float]:
    """Each event's share of the topic words (by id of its value), weighted by rarity across
    the corpus (the calendar's upcoming events)."""
    abouts = [_about(v, words) for v in corpus]
    weight = {w: math.log((len(abouts) + 1) / (sum(w in a for a in abouts) + 0.5))
              for w in words}
    total = sum(weight.values()) or 1.0
    return {id(v): sum(weight[w] for w in words & _about(v, words)) / total for v in values}


# An event found by its card's meaning (title, tags, host, place, admission, description) when
# its listing names nothing asked: it stands out from every event card for the question
# (meaning.standout_bar), is within EVENT_NEAR of the window's best event by meaning, those its
# listing names included, and is one of at most MAX_NEAR_EVENTS listings (a weekly event's dates
# count once). When the words found the event asked for, only events about as near count too
# (11-03 practice: "sexual health" found the HIV awareness event by its words, then four
# wellness and LGBTQ+ events far below it by meaning). A fixed floor of 0.44 (10-22)
# listed a dozen unrelated events for a question about club fairs (11-03 check set) and none
# for "is there a play i can go see", whose play scored 0.43.
EVENT_NEAR, MAX_NEAR_EVENTS = 0.08, 5


# A listing holding every word of what was asked (or of one alternative) has the whole topic.
WHOLE_TOPIC = 0.999

# Where a listing says every word asked only in its description, its card must be at least this
# many spreads above the median card for the question (meaning.standout_bar): clearly nearer
# than a typical listing, though it need not stand out alone. Measured on the 11-03 calendar
# (64 questions): the listings whose descriptions said the very thing asked sat 1.6 to 8 spreads
# above the median (decorating, commuters, sexual health, a veterans' group), while those that
# only mention the word sat at 1.2 or below (a therapy animal visit's QR code for "coding": 0;
# a birdwatching outing that provides binoculars and books, for "book clubs": 1.2). Three
# spreads, the bar for meaning alone, left out the cookie art and magnet decorating nights and
# the commuter lunch. The gap is narrow: on the 11-04 dev calendar the birdwatching outing came
# out above it, so words a question joins to a club's word are also checked (joined_words).
SAID_AGREES = 1.5


def joined_words(message: str, words: AbstractSet[str]) -> list[tuple[str, ...]]:
    """The topic words (`words`) a question says joined to a club's or group's word, as plain
    forms in the order said: "any book clubs meeting" says (book, club), "black student union
    events" (black, student, union). The club's word is no topic word (asked_topic), but with
    it the question names a thing ("a book club"), not a word a listing may only mention."""
    runs: list[tuple[str, ...]] = []
    run: list[str] = []
    for stem in [*(_stem(word) for word in _said(message)), ""]:
        if stem and (stem in words or stem in _GENERIC_STEMS):
            run.append(stem)
            continue
        if any(s in words for s in run) and any(s in _GENERIC_STEMS for s in run):
            runs.append(tuple(run))
        run = []
    return runs


def _passing(value: dict[str, Any], joined: Sequence[tuple[str, ...]]) -> bool:
    """Whether a listing says a topic word the question joins to a club's word (joined_words)
    but never the two together: a word said in passing, not the thing asked (11-04 practice:
    "Binoculars and books will be provided" listed a birdwatching outing for "any book clubs
    meeting")."""
    text = " ".join([str(value.get("title") or ""), " ".join(value.get("tags") or []),
                     str(value.get("description") or ""), str(value.get("organizer") or ""),
                     str(value.get("location") or "")])
    said = [_stem(word) for word in _said(text)]
    for run in joined:
        if ({s for s in run if s not in _GENERIC_STEMS} <= set(said)
                and not any(tuple(said[k:k + len(run)]) == run
                            for k in range(len(said) - len(run) + 1))):
            return True
    return False


def by_words(timed: Sequence[dict[str, Any]], share: Mapping[int, float],
             meaning: Mapping[int, float], scores: Mapping[str, float] | None,
             titled: AbstractSet[int] = frozenset(),
             joined: Sequence[tuple[str, ...]] = ()) -> set[int]:
    """The window's events (by id of their value) whose listing names what was asked: every word
    of it; or, when no listing does, most of its weight (TOPIC_SHARE). Where the words are only in
    a listing's description (`titled` holds the events whose title, tags, host or place hold
    them all), the card's meaning must agree (SAID_AGREES); where only most of them are anywhere,
    it must stand out for the question too (meaning.standout_bar). Without scores the words
    alone. A word can be the same and the thing different (11-03 practice: "saving money" found
    "a life saving drug", "sports games" a birdwatching outing tagged Sports/Recreation and a
    trading card game meeting, while the basketball games held both words; "coding workshops"
    found a therapy animal visit whose description says to scan the QR code). A description
    saying a word the question joins to a club's word (`joined`, joined_words) but never the two
    together counts no more than a listing naming nothing asked: its card must stand out."""
    values = (scores or {}).values()
    said_bar = standout_bar(values, SAID_AGREES) if scores else None
    bar = standout_bar(values) if scores else None

    def agrees(value: dict[str, Any], at: float | None) -> bool:
        return at is None or meaning[id(value)] >= at

    def said_with(value: dict[str, Any]) -> bool:
        return (not _passing(value, joined)
                or (bar is not None and meaning[id(value)] >= bar))
    whole = {id(v) for v in timed if share.get(id(v), 0.0) >= WHOLE_TOPIC
             and (id(v) in titled or (agrees(v, said_bar) and said_with(v)))}
    if whole:
        return whole
    return {id(v) for v in timed if share.get(id(v), 0.0) >= TOPIC_SHARE and agrees(v, bar)}


def near_in_meaning(candidates: Sequence[dict[str, Any]], meaning: Mapping[int, float],
                    scores: Mapping[str, float] | None) -> set[int]:
    """The candidate events (by id of their value) found by meaning: their card's score
    (`meaning`, every window event's) stands out from every event card's (`scores`), within
    EVENT_NEAR of the window's best, at most MAX_NEAR_EVENTS titles."""
    bar = standout_bar((scores or {}).values())
    if bar is None:
        return set()
    above = sorted((v for v in candidates if meaning[id(v)] >= bar), key=lambda v: -meaning[id(v)])
    if not above:
        return set()
    best = max(meaning.values())
    titles: set[str] = set()
    chosen: set[int] = set()
    for value in above:
        title = str(value.get("title") or "").casefold()
        if meaning[id(value)] < best - EVENT_NEAR or (
                title not in titles and len(titles) == MAX_NEAR_EVENTS):
            continue
        titles.add(title)
        chosen.add(id(value))
    return chosen


def _starts(value: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value.get("starts_at")))
    except ValueError:
        return None


def _ends(value: dict[str, Any], clock: Any = None) -> datetime | None:
    """When an event ends: its listed end time on its start day, else three hours after it
    starts. The listed time is the campus clock's, so the start is read on that clock first (a
    start published in UTC once made a 12:15-3:45 PM event end at 10:45 AM the next day)."""
    began = _starts(value)
    if began is None:
        return None
    if clock is not None:
        began = began.astimezone(clock)
    found = re.match(r"\s*(\d{1,2})(?::(\d{2}))?\s*([AP]M)", str(value.get("end_time") or ""), re.I)
    if not found:
        return began + timedelta(hours=3)
    hour = int(found.group(1)) % 12 + (12 if found.group(3).upper() == "PM" else 0)
    ended = began.replace(hour=hour, minute=int(found.group(2) or 0))
    return ended if ended > began else ended + timedelta(days=1)


def in_window(values: Sequence[dict[str, Any]], start: datetime,
              end: datetime) -> list[dict[str, Any]]:
    """The events that start in the window or are still on when it opens, in time order."""
    timed = [v for v in values if (at := _starts(v)) is not None and at <= end
             and (at >= start or ((ends := _ends(v, start.tzinfo)) is not None and ends > start))]
    return sorted(timed, key=lambda v: _starts(v) or start)


def rank_events(values: Sequence[dict[str, Any]], message: str, start: datetime,
                end: datetime, scores: Mapping[str, float] | None = None,
                corpus: Sequence[dict[str, Any]] | None = None, by_share: bool = False,
                kinds: Mapping[str, Sequence[str]] | None = None,
                ) -> tuple[list[dict[str, Any]], bool | None]:
    """Every event in the window that has not ended, in time order: those of the kind asked
    (asked_kinds; `kinds` is every labelled card's kinds by its "_id", when the index has them),
    those sharing the question's other topic words with their title, tags, organizer, location
    or description, and, for a question naming no kind, those whose card's meaning (scores,
    every event card's by its "_id") stands out for the question (near_in_meaning), when any
    are. Also whether the topic matched: None with no topic words, False when none matched (the
    whole window is then given, and the reply says the calendar lists nothing for the topic).
    All of them, so a reply can say how many there are: shown_events picks the ones it gives."""
    timed = in_window(values, start, end)
    food = asks_food(message)
    words = asked_topic(message)
    if words or food:
        # A kind ("any art events") is every listing of that kind, whatever words it uses
        # (11-03 practice: "art" found only Cookie Art, while the ceramics, flower and mask
        # nights were the art events); alternatives ("theater or trivia nights") each match on
        # their own, the kinds' by kind and the others by the listings' words.
        asked, rest = (asked_kinds(message, words) if kinds and words
                       else (frozenset(), alternatives(message, words)))
        listed = with_included(asked)
        kinded = {id(v) for v in timed
                  if listed & set((kinds or {}).get(str(v.get("_id")), ()))}
        share: dict[int, float] = {}
        for alternative in rest:
            for key, part in topic_share(timed, alternative, corpus or values).items():
                share[key] = max(share.get(key, 0.0), part)
        titled = {id(v) for v in timed
                  if any(alternative <= _about(v, alternative, description=False)
                         for alternative in rest)}
        meaning = {id(v): (scores or {}).get(str(v.get("_id")), 0.0) for v in timed}
        worded = (by_words(timed, share, meaning, scores, titled, joined_words(message, words))
                  if rest else set())
        # Meaning finds a single thing asked ("pet therapy"), never a whole kind: with a kind
        # asked, its labels are the list. A team's games are named by its sport, and a team
        # question asks for the team: an NFL watch party was once the "closest event" to "is
        # there a hockey team" (11-03 practice).
        near = set() if asked or asks_team(message) else near_in_meaning(
            [v for v in timed if id(v) not in worded and not (food and food_mention(v))],
            meaning, scores)
        matching = []
        for value in timed:
            # How each event matched, for the reply to say: it is of the kind asked, its listing
            # names the topic, it mentions food, or it is only near the topic in meaning.
            by = ("kind" if id(value) in kinded
                  else "words" if id(value) in worded
                  else "food" if food and food_mention(value)
                  else "meaning" if id(value) in near
                  else None)
            if by:
                matching.append({**value, "_matched_by": by})
        if matching:
            if by_share:  # Only the most on-topic, by time; an event of the kind asked is.
                def on_topic(value: dict[str, Any]) -> float:
                    return 1.0 if id(value) in kinded else round(share.get(id(value), 0.0), 2)
                top = max(on_topic(v) for v in matching)
                matching = [v for v in matching if on_topic(v) == top]
            return matching, True
        return timed, False
    return timed, None


# When a list must be cut, the events of the kind asked come first, then those whose listing
# names the topic or mentions food, then those only near it in meaning.
_MATCH_ORDER = {"kind": 0, "words": 1, "food": 1, "meaning": 2}


def shown_events(ranked: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The events a reply gives of those ranked (rank_events, in time order), at most
    MAX_EVENTS: grouped as the reply says them (match_totals' groups, the most on-topic first),
    each group in time order, so a group cut short is that group's earliest events."""
    return sorted(ranked, key=lambda v: _MATCH_ORDER.get(v.get("_matched_by") or "", 0)
                  )[:MAX_EVENTS]


def match_totals(ranked: Sequence[dict[str, Any]]) -> dict[str, int]:
    """How many of the events ranked matched each way, before shown_events cuts any: "kind",
    "words" (the listing names the topic or mentions food) and "meaning". A reply says these
    counts, never the number it shows (11-03 practice: "any art events" said the calendar
    lists 15 when 16 were labelled art, because the count was taken after the cut)."""
    by = Counter("words" if v.get("_matched_by") == "food" else v.get("_matched_by")
                 for v in ranked)
    return {key: n for key, n in by.items() if key}


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
_GENERIC_STEMS = _stems(" ".join(_GENERIC_CLUB))
# Words of a host's name that do not say which host it is ("Club", "Ramapo", "Center for"): an
# office's kind of name ("Commuter Affairs", "Office of ...", "... Services") says it is an
# office, so "events for commuters" names Commuter Affairs (11-03 practice: its scholarships
# lunch was left out).
_HOST_FILLER = _stems(" ".join(_NOT_TOPIC | _GENERIC_CLUB)
                      + " office offices center centers centre department affairs services")


def find_clubs(clubs: Sequence[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """The clubs a message names by every word of their directory name (longest first); a name
    of only generic words ("Club") names nothing."""
    words = discovery_tokens(message)
    named = [(club, core) for club in clubs
             if (core := discovery_tokens(str(club["name"]))) and core <= words
             and not core <= _NOT_TOPIC | _GENERIC_CLUB]
    named.sort(key=lambda pair: -len(pair[1]))
    return [club for club, _ in named][:6]


# A club found by its card's meaning (name, category and mission): it stands out from every club
# card for the question (meaning.standout_bar) and is within CLUB_NEAR of the best. A fixed floor
# of 0.44 (10-22) listed eight unrelated clubs for a photography club the directory lacks (11-03
# check set): its nearest card scored 0.48, and the median club card 0.38.
CLUB_NEAR, MAX_CLUBS = 0.08, 8


def clubs_by_meaning(scores: Sequence[tuple[str, float]],
                     names: dict[str, str]) -> list[dict[str, Any]]:
    """The clubs whose cards stand out for a question (`scores`: every club card's, best
    first), near the best, at most MAX_CLUBS ("cultural clubs" lists several, "is there a
    cooking club" the one that bakes); none when no card stands out."""
    bar = standout_bar(score for _, score in scores)
    if bar is None or not scores or scores[0][1] < bar:
        return []
    return [{"id": entity_id, "name": names[entity_id]} for entity_id, score in scores
            if score >= max(bar, scores[0][1] - CLUB_NEAR) and entity_id in names
            ][:MAX_CLUBS]


MAX_CATEGORY_CLUBS = 25
_GENERIC_CATEGORY = {"student organization", "department", "office sponsored organization"}


def _category_head(entry: Mapping[str, Any]) -> str:
    return str(entry.get("category") or "").split(" - ")[0].strip().casefold()


def names_its_kind(entry: Mapping[str, Any]) -> bool:
    """Whether a directory entry's name only says its own category ("Honor Societies", listed
    under Honor Society - Academic): it stands for the kind, not one club, so a question naming
    it asks for the kind (11-03 practice: "are there any honor societies" was answered with that
    one entry, and none of the 31 honor societies was named)."""
    name = _kind_words(entry)
    return bool(name) and name <= _stems(str(entry.get("category") or ""))


def _kind_words(entry: Mapping[str, Any]) -> set[str]:
    return _stems(str(entry.get("name") or "")) - _stems("ramapo college rcnj")


def kind_members(found: Sequence[dict[str, Any]],
                 directory: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The clubs found, each entry that names its kind (names_its_kind) followed by every
    directory club of that kind; each club once, at most MAX_CATEGORY_CLUBS."""
    by_id = {str(e.get("id")): e for e in directory}
    chosen: dict[str, dict[str, Any]] = {}
    for club in found:
        chosen.setdefault(str(club.get("id")), club)
        entry = by_id.get(str(club.get("id")))
        if entry is not None and names_its_kind(entry):
            for member in directory:
                if _category_head(member) == _category_head(entry):
                    chosen.setdefault(str(member.get("id")),
                                      {"id": member.get("id"), "name": member.get("name")})
    return list(chosen.values())[:MAX_CATEGORY_CLUBS]


def kind_named(message: str, directory: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The directory entry named for its kind ("Honor Societies": names_its_kind) that a question
    names in any form ("an honor society"), when it names no other club; None otherwise."""
    said = _stems(message)
    kinds = [e for e in directory if names_its_kind(e) and _kind_words(e) <= said]
    others = [c for c in find_clubs(directory, message) if c not in kinds]
    return kinds[0] if len(kinds) == 1 and not others else None


def members_of(entry: Mapping[str, Any],
               directory: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every directory club listed under the category an entry named for its kind stands for,
    but such entries themselves ("Honor Societies" stands for 31 honor societies)."""
    return [e for e in directory
            if _category_head(e) == _category_head(entry) and not names_its_kind(e)]


def kind_head(entry: Mapping[str, Any]) -> str:
    """The category an entry named for its kind stands for, as the directory writes it."""
    return str(entry.get("category") or "").split(" - ")[0].strip()


def only_kind_asked(message: str, entry: Mapping[str, Any]) -> bool:
    """Whether a question asks for nothing but the clubs of the kind an entry is named for."""
    return asked_topic(message) <= _stems(str(entry.get("name") or ""))


def club_list_text(lead: str, clubs: Sequence[dict[str, Any]],
                   inactive: Mapping[int, dict[str, Any]] | None = None) -> str:
    """Code's list of every club of a kind asked, after its `lead` ("The club directory lists 31
    clubs under Honor Society [1]"): each club by its name [its number], one per line, and beside
    one a ramapo.edu page marks inactive, that page's word [its number] (`inactive`, by the
    club's number). A list given to the model came back cut short (11-03 practice: given 25
    honor societies, the reply named 5)."""
    lines = []
    for club in clubs:
        status = (inactive or {}).get(club["n"])
        lines.append(f"- {literal(club['name'])} [{club['n']}]"
                     + (f", but Ramapo's page for it says it is not currently an active club, "
                        f"as of {_as_of(str(status['as_of']))} [{status['n']}]"
                        if status else ""))
    return "\n\n".join([f"{lead}:", "\n".join(lines)])


def club_category(found: Sequence[dict[str, Any]],
                  directory: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every club of the category most of the clubs found share, when it is a specific one
    ("Greek Life", "Honor Society": not "Student Organization"): a question about a kind of
    club wants the kind, not the few nearest. Empty when no such category leads."""
    head = _category_head
    heads = [head(e) for e in found if head(e)]
    if len(heads) < 3:
        return []
    top = max(set(heads), key=heads.count)
    if top in _GENERIC_CATEGORY or heads.count(top) * 2 < len(found):
        return []
    return [e for e in directory if head(e) == top]


def of_shared_kind(found: Sequence[dict[str, Any]],
                   kinds: Mapping[str, Sequence[str]] | None) -> list[dict[str, Any]]:
    """The clubs found by meaning for one searched text that have the kind most of them share
    (kinds.py), when three or more are found and most share one: a club near only in its words
    is of another kind (11-03 practice: "is there a ballet club" found three dance clubs and the
    Balkan Club, a cultural club whose card scored near theirs). All of them otherwise."""
    if not kinds or len(found) < 3:
        return list(found)
    held = [set(kinds.get(str(club.get("id")), ())) for club in found]
    counts = Counter(kind for kinds_held in held for kind in kinds_held)
    shared = {kind for kind, n in counts.items() if n * 2 > len(found)}
    if not shared:
        return list(found)
    return [club for club, kinds_held in zip(found, held, strict=True) if kinds_held & shared]


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
                     calendar_n: int | None = None, asked: str = "", kinds_said: str = "",
                     only_kinds: bool = False, totals: Mapping[str, int] | None = None,
                     ) -> list[dict[str, Any]]:
    """What code works out from the calendar for the reply to state as it is: the window's
    dates; how many events it holds, how many are about the topic asked or that none is (and
    the topic's events outside the window); for a food question, which listings mention food
    and what they say. `events` are the window's lines given (with "n" and the published
    value under "_value"); `outside` the topic's lines after the window; `calendar_n` the
    calendar page's number, for the facts about the whole window; `asked` the question, whose
    own words the facts say; `kinds_said` the kinds asked (kinds.names), `only_kinds` when the
    question asked nothing else; `totals` how many matched each way before the lines were cut
    (match_totals), when more matched than are given."""
    span = (_day(start) if start.date() == end.date()
            else f"{_day(start)} to {_day(end)}")
    where = f"on {span}" if start.date() == end.date() else f"from {span}"
    topic = (said_words(asked, words) if asked
             else ", ".join(sorted(w for w in words if len(w) > 2)))
    facts: list[dict[str, Any]] = [{"fact": f"The window asked ({label}) is {span}.",
                                    "n": calendar_n}]
    shown = len(events)
    if matched is None:
        facts.append({"fact": f"The calendar lists {in_window} event"
                              f"{'s' if in_window != 1 else ''} {where}"
                              + (f"; the first {shown} in time order are given."
                                 if shown < in_window else "."), "n": calendar_n})
    elif matched:
        kinded = [line for line in events if (line.get("_value") or {}).get("_matched_by")
                  == "kind"]
        named = [line for line in events if (line.get("_value") or {}).get("_matched_by")
                 in ("words", "food")]
        near = [line for line in events if (line.get("_value") or {}).get("_matched_by")
                == "meaning"]
        about = (f"whose listing names {topic}" + (" or mentions food" if food else "")
                 if topic else "whose listing mentions food")
        total = {"kind": len(kinded), "words": len(named), **(totals or {})}
        if kinded:
            facts.append({"fact": f"By what their listings describe, the calendar lists "
                                  f"{total['kind']} {kinds_said} event"
                                  f"{'s' if total['kind'] != 1 else ''} {where} (of {in_window} "
                                  f"events then){_given(len(kinded), total['kind'])}",
                          "n": calendar_n})
        if total["words"] or not (near or kinded):
            facts.append({"fact": f"The calendar lists {total['words']} event"
                                  f"{'s' if total['words'] != 1 else ''} {about} {where} (of "
                                  f"{in_window} events then){_given(len(named), total['words'])}",
                          "n": calendar_n})
        elif near:
            # Only when some are near in meaning: events of the kind asked are what was asked
            # (11-03 practice: "any greek life events" was said to be only near matches).
            facts.append({"fact": f"No listing {where} holds most of what was asked ({topic}); "
                                  f"of the {in_window} events then, the ones below are only near "
                                  "it in meaning.", "n": calendar_n})
        if near:
            facts.append({"fact": f"Only near {topic or 'the question'} in meaning, not named "
                                  "by their listings: "
                                  + "; ".join(f"{line['event']} [{line['n']}]" for line in near)
                                  + ".", "n": near[0]["n"]})
        if outside:
            near_only = all(line.get("near_in_meaning") for line in outside)
            facts.append({"fact": f"After {span}, the calendar's next events "
                                  + (f"only near {topic or 'the question'} in meaning, not named "
                                     "by their listings" if near_only else about)
                                  + ", by date: "
                                  + "; ".join(f"{line['event']} ({line['when']}) [{line['n']}]"
                                              for line in outside) + ".",
                          "n": outside[0]["n"]})
    else:
        about = (f"about {topic}" if topic else "whose listing mentions food" if food
                 else "about what was asked")
        none, next_ones, never = (
            (f"no {kinds_said} event", f"next {kinds_said} events",
             f"No upcoming {kinds_said} event is on the whole calendar.") if only_kinds
            else (f"no event {about}", f"next events {about}",
                  f"No upcoming event on the whole calendar is {about}."))
        facts.append({"fact": f"The calendar lists {none} {where}; it lists "
                              f"{in_window} other event{'s' if in_window != 1 else ''} then.",
                      "n": calendar_n})
        if outside:
            facts.append({"fact": f"Outside {span}, the calendar's {next_ones}, by date: "
                                  + "; ".join(f"{line['event']} ({line['when']}) [{line['n']}]"
                                              for line in outside) + ".",
                          "n": outside[0]["n"]})
        else:
            facts.append({"fact": never, "n": calendar_n})
    if food:
        with_food = [(line, said) for line in events
                     if (said := food_mention(line.get("_value") or {}))]
        for line, said in with_food:
            facts.append({"fact": f"{line['event']} ({line['when']}) mentions food in its "
                                  f'listing: "{said}"', "n": line["n"]})
        if not with_food and topic:
            facts.append({"fact": f"No event listing {where} mentions food.", "n": calendar_n})
    return facts


def _given(shown: int, of: int) -> str:
    """The end of a count's fact: which of them the reply gives, when not all."""
    if shown >= of:
        return "."
    return (f"; the first {shown} in time order are given." if shown
            else f"; not given here (the calendar has {'it' if of == 1 else 'them'}).")


def _phrase(label: str, start: datetime, end: datetime) -> str:
    """The window as the reply says it: "this weekend, Friday, November 6 to Sunday, November 8",
    "tonight, Monday, November 2", "on Friday, November 6, in the evening"."""
    span = _day(start) if start.date() == end.date() else f"{_day(start)} to {_day(end)}"
    base, part = label, ""
    for _, _, _, name in _PART_OF_DAY:
        if label.endswith(f", {name}"):
            base, part = label[: -len(name) - 2], f", in the {name}"
    if base.split(",")[0].casefold() in _DAYS:
        return f"on {span}{part}"
    lead = {"the next": "in ", "the rest": "for "}.get(" ".join(base.split()[:2]), "")
    return f"{lead}{base}, {span}{part}"


def listing_text(label: str, start: datetime, end: datetime, shown: Sequence[dict[str, Any]],
                 listed: int) -> str | None:
    """A window question's answer in code's own words (Dan 11-02: code works out the answer):
    how many events the calendar lists in the window [1], then those shown in time order, under
    their day when the window spans several, each with its time, title, host and place as
    published [its own number, from 2]. None when an event has no title to say."""
    phrase = _phrase(label, start, end)
    if not listed:
        return f"The calendar lists no events {phrase} [1]."
    if any(not value.get("title") for value in shown):
        return None
    head = (f"The calendar lists {listed} event{'s' if listed != 1 else ''} {phrase}"
            + (f"; here are the first {len(shown)} by time [1]:" if len(shown) < listed
               else " [1]:"))
    return "\n\n".join([head, *_event_lines(list(enumerate(shown, start=2)), start, end)])


def _event_lines(numbered: Sequence[tuple[int, dict[str, Any]]], start: datetime,
                 end: datetime) -> list[str]:
    """Events as code says them, in the order given: under their day when the window spans
    several days, each with its time, title, host and place as published [its number]."""
    days: dict[str, list[str]] = {}
    for n, value in numbered:
        began = _starts(value)
        on = began.astimezone(start.tzinfo).date() if began is not None else None
        heading = (f"{on:%A, %B} {on.day}:" if on is not None and start.date() != end.date()
                   else "")
        when = " to ".join(str(t) for t in (value.get("start_time"), value.get("end_time")) if t)
        place = " ".join(str(value.get("location") or "").split())
        at = "" if not place or place == "-" else (
            f", {literal(place)}" if _ONLINE.match(place) else f", at {literal(place)}")
        host = " ".join(str(value.get("organizer") or "").split())
        days.setdefault(heading, []).append(
            "- " + (f"{when}: " if when else "") + literal(value["title"])
            + (f" ({literal(host)})" if host else "") + at + f" [{n}]")
    return ["\n".join([heading, *lines]) if heading else "\n".join(lines)
            for heading, lines in days.items()]


# An upcoming event whose title names all that a question not about events asks is said after
# the reply, in code's words (engine._with_related_events). 11-03 practice: "Anything about
# Narcan?" gave the places to get naloxone and missed "You Can Narcan!", because the question
# asks for no event and the calendar was not read. Dan (11-03) chose one code-written line for
# such an event, limited to uncommon words so "library" doesn't pull in every library event: at
# least one of the words is said by at most RELATED_LISTINGS of the calendar's listings and
# RELATED_HEADINGS of the published pages' sections. Measured on the 11-03 calendar (233
# listings) and pages (10,693 sections): "narcan" is in 1 listing and 1 section heading, "condom"
# 1 and 0, "kosher" 1 and 0; "library" is in 458 headings, "parking" 126, "job" 214 and
# "tutoring" 9 (a tutoring question is not followed by a volunteer tutoring outing), and "grab"
# in 10 listings ("grab a snack"), "stress" 6, "health" 12. Tried on 65 everyday questions and
# the 50 of the 11-03 repeat test: a line followed only questions about Narcan, condoms, kosher
# food, Chabad, a farmers market, a passport, a food pantry and Thanksgiving. A game's title
# names its teams, not what it is about.
RELATED_DAYS = 30
MAX_RELATED = 2
RELATED_LISTINGS = 3
RELATED_HEADINGS = 3
RELATED_LEAD = "Coming up on the campus calendar:"
# Words asking what is known about a thing ("any info on", "learn more about", "how do I use
# it"), not naming it.
_KNOWN_ASKED = _stems("info information detail details learn more use using")


def related_topic(message: str) -> set[str]:
    """The words an upcoming event's title must hold to be about what a question asks: its topic
    words (topic_words), less those asking what is known (_KNOWN_ASKED), naming an event's form
    (_FORM) or a host's kind (_GENERIC_STEMS), or a subject code answers from its own records
    (a menu, hours, the shuttle, the weather: _SUBJECT_WORDS)."""
    return (topic_words(message) - _KNOWN_ASKED - _FORM - _GENERIC_STEMS
            - _stems(" ".join(_SUBJECT_WORDS)))


def listing_words(values: Sequence[dict[str, Any]]) -> Counter[str]:
    """How many of the calendar's listings say each word, in their title, tags, description or
    place; a listing on several dates counts once (by its title). A host's name says who runs an
    event, not what it is about."""
    said: dict[str, set[str]] = {}
    for value in values:
        title = " ".join(str(value.get("title") or "").casefold().split())
        text = " ".join([title, " ".join(value.get("tags") or []),
                         str(value.get("description") or ""), str(value.get("location") or "")])
        for word in _stems(text):
            said.setdefault(word, set()).add(title)
    return Counter({word: len(titles) for word, titles in said.items()})


def heading_words(labels: Iterable[Sequence[str]]) -> Counter[str]:
    """How many of the published pages' sections say each word in their page's place on the
    site or their heading (the meaning index's labels)."""
    return Counter(word for label in labels for word in _stems(" ".join(label)))


def related_events(values: Sequence[dict[str, Any]], words: AbstractSet[str], now: datetime,
                   listings: Mapping[str, int], headings: Mapping[str, int],
                   ) -> list[dict[str, Any]]:
    """The events of the next RELATED_DAYS days whose title holds every word asked
    (related_topic), when at least one of the words is uncommon (RELATED_LISTINGS,
    RELATED_HEADINGS): soonest first, each title once, at most MAX_RELATED, and no game."""
    if not any(listings.get(word, 0) <= RELATED_LISTINGS
               and headings.get(word, 0) <= RELATED_HEADINGS for word in words):
        return []
    found: list[dict[str, Any]] = []
    titles: set[str] = set()
    for value in in_window(values, now, now + timedelta(days=RELATED_DAYS)):
        title = " ".join(str(value.get("title") or "").split())
        if (title and title.casefold() not in titles and not _VERSUS.search(title)
                and words <= _stems(title)):
            titles.add(title.casefold())
            found.append(value)
    return found[:MAX_RELATED]


def related_text(numbered: Sequence[tuple[int, dict[str, Any]]], now: datetime) -> str:
    """The events related_events found, as code says them after a reply: under their day, each
    with its time, title, host and place as published [its number]."""
    return "\n\n".join([RELATED_LEAD,
                        *_event_lines(numbered, now, now + timedelta(days=RELATED_DAYS))])


def topic_text(label: str, start: datetime, end: datetime, in_window: int,
               lines: Sequence[dict[str, Any]], matched: bool | None,
               outside: Sequence[dict[str, Any]] = (), calendar_n: int = 1,
               kinds_said: str = "", only_kinds: bool = False,
               totals: Mapping[str, int] | None = None) -> str | None:
    """A calendar question's events in code's own words, every one code chose (11-03: the reply
    picked 2 of the 3 wellness events and said "three"): with no topic, the window's listing;
    with a topic, the events of the kind asked (`kinds_said`, kinds.names), then those whose
    listing matches it, then those only near it in meaning; with none matching, that the calendar
    lists none then, the topic's next later dates, and the window's events. `lines` are the
    window's chosen events (each with "n" and the published value under "_value"), `outside` the
    topic's later ones; `only_kinds` when the question asked nothing but kinds; `totals` how many
    matched each way before the lines were cut (match_totals): each group says its whole count,
    and that the ones given are its first by time when not all are. None when an event has no
    title."""
    if any(not (line.get("_value") or {}).get("title") for line in [*lines, *outside]):
        return None
    phrase = _phrase(label, start, end)
    cal = f"[{calendar_n}]"

    def said(chosen: Sequence[dict[str, Any]], first: datetime, last: datetime) -> list[str]:
        return _event_lines([(line["n"], line["_value"]) for line in chosen], first, last)
    if matched is None:
        if not in_window:
            return f"The calendar lists no events {phrase} {cal}."
        head = (f"The calendar lists {in_window} event{'s' if in_window != 1 else ''} {phrase}"
                + (f"; here are the first {len(lines)} by time {cal}:" if len(lines) < in_window
                   else f" {cal}:"))
        return "\n\n".join([head, *said(lines, start, end)])
    if matched:
        kinded = [line for line in lines
                  if (line.get("_value") or {}).get("_matched_by") == "kind"]
        named = [line for line in lines
                 if (line.get("_value") or {}).get("_matched_by") in ("words", "food")]
        near = [line for line in lines
                if (line.get("_value") or {}).get("_matched_by") == "meaning"]
        parts: list[str] = []
        total = {"kind": len(kinded), "words": len(named), **(totals or {})}

        def first(shown: int, of: int) -> str:
            return f"; here are the first {shown} by time" if shown < of else ""
        if kinded:
            n = total["kind"]
            parts += [f"The calendar lists {n} {kinds_said} event{'s' if n != 1 else ''} "
                      f"{phrase} (of {in_window} events then){first(len(kinded), n)} {cal}:",
                      *said(kinded, start, end)]
        n = total["words"]
        if named:
            parts += [(f"The calendar lists {n} event{'s' if n != 1 else ''} {phrase} that "
                       f"match{'es' if n == 1 else ''} what you asked (of {in_window} events "
                       f"then){first(len(named), n)} {cal}:") if not kinded else
                      "Also matching what you asked, by their listings' words"
                      + (f" ({n} events, the first {len(named)} by time):" if len(named) < n
                         else ":"),
                      *said(named, start, end)]
        elif n:
            parts.append(f"The calendar also lists {n} more event{'s' if n != 1 else ''} then "
                         + ("whose listing names" if n == 1 else "whose listings name")
                         + f" what you asked {cal}.")
        if near:
            parts += [("These are only near it in meaning, not named by their listings:"
                       if named else
                       f"No event {phrase} is listed by name for what you asked; these are the "
                       f"closest in meaning (of {in_window} events then) {cal}:"),
                      *said(near, start, end)]
        later = [line for line in outside if _starts(line["_value"]) is not None]
        if parts and later:
            # No time was named, so the two weeks are the calendar's default, not the student's:
            # the topic's next later dates follow, always said under their day, and said as only
            # near in meaning when none of them is named by its listing.
            near_only = all((line.get("_value") or {}).get("_matched_by") == "meaning"
                            for line in later)
            parts += [("Later on the calendar, only near it in meaning:" if near_only
                       else "Later on the calendar, also matching what you asked:"),
                      *said(later, start, start + timedelta(days=1))]
        return "\n\n".join(parts) if parts else None
    parts = [f"The calendar lists no {kinds_said} events {phrase} {cal}." if only_kinds
             else f"The calendar lists no event {phrase} that matches what you asked {cal}."]
    if outside:
        later = [line for line in outside if _starts(line["_value"]) is not None]
        if later:
            # Later dates are always said under their day.
            parts += ["Later on the calendar:", *said(later, start, start + timedelta(days=1))]
    # The window's other events only for a time the question named: "is there a quidditch
    # tournament" is not answered with the next two weeks' 114 events.
    if lines and label != DEFAULT_LABEL:
        parts += [f"All {in_window} event{'s' if in_window != 1 else ''} {phrase}"
                  + (f", the first {len(lines)} by time:" if len(lines) < in_window else ":"),
                  *said(lines, start, end)]
    return "\n\n".join(parts)


# A place that is no room ("Online Event", "Virtual (Register on Handshake for Details)") is said
# without "at".
_ONLINE = re.compile(r"(?:online|virtual|zoom|remote)\b", re.I)


# What a club has on asks for its events: when it meets, its next events, "anything going on with
# them soon" (fix check 11-04: "is there a jewish student group? anything going on with them
# soon" was read as asking only whether the club exists, and its events were never read). "I like
# going on hikes" asks for none.
_CLUB_TIME_ASK = re.compile(r"\b(?:when|meet|meets|meeting|meetings|perform\w*|events?|"
                            r"schedule|next|upcoming|coming up|soon|"
                            r"(?:anything|something|what'?s|whats|what is)\s+(?:[\w'’]+\s+)?"
                            r"(?:going on|happening)|"
                            r"(?:going on|happening)\s+(?:with|at|for|this|next|today|tonight|"
                            r"tomorrow))\b")


def asks_club_events(message: str) -> bool:
    return bool(_CLUB_TIME_ASK.search(message.casefold()))


def club_answers(clubs: Sequence[dict[str, Any]], message: str, by_name: bool,
                 directory_size: int) -> list[dict[str, Any]]:
    """What code works out about the clubs a question asks for: each club found by its name
    as the directory lists it; for clubs found only by meaning, whether any has a word asked in
    its name, and which say every word of it in their entries (else they are only the nearest);
    or that the directory lists no club for what was asked. Every club of a kind asked is code's
    own list (club_list_text). Whether a club found by meaning is what was asked ("Astronomy
    Club" for space, "Culinary and Pastry Skills" for baking) is the reply's to judge from the
    entries: words alone cannot say it, so code never says a club found by meaning is or is not
    one (11-03 practice: code's "lists no club for space" was wrong)."""
    words = topic_words(message) - _GENERIC_STEMS
    topic = topic_span(message, words)
    if not clubs:
        return [{"fact": f"The club directory ({directory_size} clubs and organizations) lists "
                         f"no club for {topic or 'what was asked'}."}]
    facts: list[dict[str, Any]] = []
    if not by_name and words and not any(words & _stems(str(c.get("name") or ""))
                                         for c in clubs):
        # An entry may say the topic though no name does ("[Previously Sci Fi and Comic Book
        # Club]", "fans of games, anime ..."): the reply says both, so "is there an anime club"
        # learns that none is named for it and which club's entry covers it.
        saying = [c for c in clubs if words <= _stems(str(c.get("mission") or ""))]
        said = "; ".join(f"{c.get('name')} [{c['n']}]" for c in saying)
        facts.append({"fact": f"No club in the directory has {topic} in its name"
                              + (f"; the entry of {said} says {topic}" if len(saying) == 1
                                 else f"; the entries of {said} say {topic}" if saying else "")
                              + "; the " + ("other " if saying else "")
                              + "clubs given are only the nearest by meaning."})
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


def topic_span(message: str, words: AbstractSet[str]) -> str:
    """What a question asks about in its own words, from the first topic word it says to the
    last ("comic books", "hiking and camping"), for a reply to say; empty with none."""
    said = [w for w in said_words(message, words).split(", ") if w]
    if not said:
        return ""
    text = message.casefold()
    first = re.search(rf"\b{re.escape(said[0])}\b", text)
    last = re.search(rf"\b{re.escape(said[-1])}\b", text[first.start():]) if first else None
    return (text[first.start():first.start() + last.end()] if first and last
            else " ".join(said))


# A question asking only what a club the directory names in full hosts next ("when does the
# Hiking Club meet", "what events does the Math Club have coming up") is answered by code when the
# calendar lists events it hosts (club_events_text).
_CLUB_FILLER = discovery_tokens("when what does do is are there any you we they have ramapo "
                                "college rcnj school campus on at here in its next upcoming "
                                "coming up meet meets meeting meetings event events hold holds "
                                "host hosting") | _GENERIC_CLUB


def club_meets(message: str, directory: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The club a question only asks the meetings or next events of, when it names the club by
    every word of its directory name and asks nothing else; None otherwise."""
    if not asks_club_events(message):
        return None
    for club in find_clubs(directory, message)[:1]:
        rest = re.sub(re.escape(str(club["name"]).casefold()), " ", message.casefold())
        if (not re.search(r"\b(?:and|also|plus|or|where|how|why|who)\b", rest)
                and discovery_tokens(message) - discovery_tokens(str(club["name"]))
                <= _CLUB_FILLER):
            return club
    return None


# Whether a club a question names in full exists ("is there a math club?", "does Ramapo have a
# Black Student Union?") is code's once the release marks inactive clubs: ramapo.edu says some clubs
# the directory still lists are not active (Garden Club, Movie Club, as of 6/12/25), and both are
# said when they differ (club_text). A club named in part or a kind of club goes to the model.
_CLUB_EXISTS = re.compile(r"^\W*(?:is there|are there|does (?:ramapo|the college|rcnj|the school|"
                          r"campus) have|do (?:you|we|they) have)\b")
_EXISTS_FILLER = discovery_tokens("is are there any does do you we they have ramapo college rcnj "
                                  "school campus on at here in called named still active "
                                  "anymore") | _GENERIC_CLUB


def asks_club_exists(message: str) -> bool:
    return bool(_CLUB_EXISTS.search(message.casefold()) and asks_clubs(message))


# A club's name may start with the college's own ("Ramapo College Chess Club"); a student says
# "chess club".
_CAMPUS_PREFIX = re.compile(r"^(?:ramapo college(?: of new jersey)?|ramapo|rcnj)\s+", re.I)


def club_named(message: str, clubs: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The one club an existence question names by every word of its name (the college's own
    name before it optional) and asks nothing else of; None otherwise."""
    if not _CLUB_EXISTS.search(message.casefold()):
        return None
    short = [{**club, "name": _CAMPUS_PREFIX.sub("", str(club["name"])) or club["name"],
              "_named": club} for club in clubs]
    # Students call a club sports entry a team: "is there an ice hockey team" names the Ice Hockey
    # Club (never the other way: "dance clubs" does not name the Dance Team alone).
    said = f"{message} club" if asks_team(message) else message
    for club in find_clubs(short, said)[:1]:
        rest = re.sub(re.escape(str(club["name"]).casefold()), " ", message.casefold())
        if (not re.search(r"\b(?:and|also|plus|or|how|why|who|when|where)\b", rest)
                and discovery_tokens(message) - discovery_tokens(str(club["_named"]["name"]))
                <= _EXISTS_FILLER):
            return dict(club["_named"])
    return None


def _first_sentence(text: Any) -> str | None:
    """The first whole sentence of a published text (the directory cuts its missions short)."""
    found = re.match(r"\s*(.{20,300}?(?<!\bInc)(?<!\bDr)(?<!\bSt)(?<!\bvs)[.!?])(?=\s|$)",
                     " ".join(str(text or "").split()))
    return found.group(1) if found else None


def _as_of(value: str) -> str:
    day = date.fromisoformat(value)
    return f"{day:%B} {day.day}, {day.year}"


def club_text(name: str, entry: dict[str, Any] | None,
              status: dict[str, Any] | None) -> str | None:
    """Code's answer to whether a club exists: the directory's listing [its number] (its category,
    the first sentence of its mission and its email), and when a ramapo.edu page marks the club
    inactive, that page's word [its number] beside it. None when neither says anything."""
    inactive = (status or {}).get("state") == "inactive" and (status or {}).get("as_of")
    said: list[str] = []
    if entry is not None:
        listing = (f"The club directory lists {literal(entry['name'])}"
                   + (f" ({literal(entry['category'])})" if entry.get("category") else "")
                   + f" [{entry['n']}]")
        if inactive and status is not None:
            return (f"{listing}, but Ramapo's page for it says it is not currently an active club, "
                    f"as of {_as_of(str(status['as_of']))} [{status['n']}].")
        said.append(f"Yes. {listing}.")
        if sentence := _first_sentence(entry.get("mission")):
            said.append(f'Its page says: "{literal(sentence)}" [{entry["n"]}]')
        if entry.get("email"):
            said.append(f"Email: {entry['email']} [{entry['n']}].")
        return " ".join(said)
    if inactive and status is not None:
        return (f"Ramapo's page for {literal(name)} says it is not currently an active club, as of "
                f"{_as_of(str(status['as_of']))} [{status['n']}]. The club directory does not "
                f"list it.")
    return None


def club_events_text(club: dict[str, Any], lines: Sequence[dict[str, Any]]) -> str:
    """Code's answer to what a club hosts next: the calendar's upcoming events it hosts, each with
    its date and time, title and place as published [its number]."""
    listed = [f"- {line['when']}: {literal(line['event'])}"
              + (f", at {literal(place)}" if (place := " ".join(
                  str(line.get("location") or "").split())) and place != "-" else "")
              + f" [{line['n']}]" for line in lines]
    return "\n\n".join([f"The calendar's next events hosted by {literal(club['name'])} "
                          f"[{club['n']}]:", "\n".join(listed)])
MAX_OUTSIDE = 3


MAX_WORD_CLUBS = 12


def clubs_by_words(directory: Sequence[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """The directory clubs whose name has a word the question asks about ("arts" for an art
    club question, "commuter" for Commuter Affairs), at most MAX_WORD_CLUBS."""
    words = {w for w in topic_words(message) - _GENERIC_STEMS if len(w) > 2}
    return [c for c in directory if words & _stems(str(c.get("name") or ""))][:MAX_WORD_CLUBS]


# The college's own names: its directory entry is the college, not a club.
_COLLEGE_NAMES = frozenset({"ramapo college of new jersey", "ramapo college", "ramapo", "rcnj"})


def clubs_on_pages(directory: Sequence[dict[str, Any]], text: str,
                   ordinary: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The directory clubs a page given names by their whole directory name ("Black Student
    Union", "Naach"); a one-word name only when it is not an ordinary word of the pages. The
    college's own entry is no group a page names (every page says "Ramapo College of New
    Jersey"), and a name said only inside a longer club's name is not said: "Phi Alpha Delta"
    does not name Phi Alpha (11-03 practice: both were listed among the fraternities and
    sororities)."""
    folded = " ".join(re.findall(r"[^\W_]+", text.casefold()))
    found: list[tuple[dict[str, Any], list[tuple[int, int]]]] = []
    for club in directory:
        words = re.findall(r"[^\W_]+", str(club.get("name") or "").casefold())
        if not words or set(words) <= _GENERIC_CLUB | {"ramapo", "the", "of", "and"}:
            continue
        if " ".join(words) in _COLLEGE_NAMES:
            continue
        if len(words) == 1 and (len(words[0]) < 5 or words[0] in ordinary):
            continue
        spans = [m.span() for m in re.finditer(rf"\b{re.escape(' '.join(words))}\b", folded)]
        if spans:
            found.append((club, spans))
    every = [span for _, spans in found for span in spans]
    return [club for club, spans in found
            if any(not any(a <= start and end <= b and (a, b) != (start, end) for a, b in every)
                   for start, end in spans)]
