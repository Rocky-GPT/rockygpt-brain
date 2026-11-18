"""Academic calendar dates: which term and which kind of date a question asks about, and code's
answer from the calendar's own entries.

Ramapo's academic calendar pages list each term's dates ("Fall 2026": classes begin, the add/drop
and withdrawal deadlines, breaks, final exams), each entry with its title and day as published and
the kind of date it is (rockygpt-data calendar-concepts.ts). A question asking only when one kind
of date is ("when are finals?", "last day to withdraw this semester", "when does the spring
semester start?") is answered by code: every entry of that kind in the term asked, in date order,
with no model call. Anything else, and a kind the term lists no entry of, is left to the model,
which reads the calendar pages.

Which kind of date, which term and which break are the read's picks (asked_time.py, tools review
11-18), not word rules: with no read, code says no calendar date and the model reads the pages.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from rockygpt_brain.asked_time import AskedTime
from rockygpt_brain.plain_writer import literal

# Words of a plain calendar question (the read picks which kind, term and break they name); any
# other word means it asks more ("if I'm a transfer student"), and the model reads it.
_PLAIN = frozenset("""
when is are was the a an what whats which day date dates last first of to for do does did i we
my this next current semester term session year classes class course courses start starts begin
begins end ends finish finishes over break finals final exam exams examinations week ramapo
college at in on deadline deadlines due grades grade add drop withdraw withdrawal withdrawing from
a refund refunds tuition thanksgiving spring winter summer fall autumn recess audit pass fail
grading option options
""".split())
# Whether a date is still ahead, asked without "when" ("can I still add a class", "is it too
# late to withdraw"): the date of that kind is what answers it, marked as passed or still to come.
_STILL = re.compile(r"\bcan (?:i|we|you) still\b|\b(?:is it|am i|are we) (?:still )?(?:too late|in "
                    r"time)\b|\btoo late\b|\bstill (?:able|possible|time|open)\b|\banymore\b|"
                    r"\bmissed\b|\bin time\b")


def _kinds_in(picked: str | None) -> list[str]:
    """The kind of date a question names: the read's pick (asked_time.py); none when it picked
    none or there was no read."""
    return [] if picked in (None, "none") else [str(picked)]


def kind_asked(message: str, picked: str | None = None) -> str | None:
    """The one kind of calendar date a question asks when it is, or None. `picked` is the read's
    kind of date (asked_time.py); with no read, None."""
    text = message.casefold().replace("’", "'")
    if not re.search(r"\bwhen\b|\bwhat (?:day|date)\b|\blast day\b|\bdeadline\b", text):
        return None
    found = _kinds_in(picked)
    words = re.findall(r"[a-z0-9]+", text.replace("'", ""))
    if len(found) != 1 or any(w not in _PLAIN and not re.fullmatch(r"20\d{2}", w)
                              for w in words):
        return None
    return found[0]


def kind_named(message: str, picked: str | None = None) -> str | None:
    """The one kind of calendar date a question names when it asks when or a deadline, whatever
    else it asks ("what's the deadline to withdraw and what shows on my transcript"): code says
    those dates first and the model the rest (11-03: the reply gave a page's 7-week dates and left
    out the calendar's full-semester Nov 6 deadline). None for no kind or two."""
    text = message.casefold().replace("’", "'")
    if not (re.search(r"\bwhen\b|\bwhat (?:day|date)\b|\blast day\b|\bdeadlines?\b|"
                      r"\bdue date\b|\bby when\b|\bhow late\b|\buntil when\b", text)
            or _STILL.search(text)):
        return None
    found = _kinds_in(picked)
    return found[0] if len(found) == 1 else None


def term_spans(dates: Sequence[dict[str, Any]]) -> dict[str, tuple[date, date]]:
    """Each term's first and last listed day, by the term names its dates carry."""
    spans: dict[str, tuple[date, date]] = {}
    for entry in dates:
        try:
            day = date.fromisoformat(str(entry.get("date")))
        except ValueError:
            continue
        for name in entry.get("terms") or []:
            first, last = spans.get(str(name), (day, day))
            spans[str(name)] = (min(first, day), max(last, day))
    return spans


def term_asked(message: str, terms: Sequence[dict[str, Any]],
               spans: dict[str, tuple[date, date]], today: date,
               picked: str | None = None) -> dict[str, Any] | None:
    """The term a question asks about, by the read's term pick (`picked`, asked_time.py): a
    season ("spring", "fall 2026"; a season alone is its term in session or the next to start,
    and a year is read from the question's numbers), the fall or spring after the current one
    ("next"), else the fall or spring in session today, else the next term to start."""
    text = message.casefold()
    dated = sorted((t for t in terms if t.get("name") in spans),
                   key=lambda t: spans[str(t["name"])][0])

    def on(term: dict[str, Any]) -> bool:
        first, last = spans[str(term["name"])]
        return first <= today <= last
    current = (next((t for t in dated if on(t) and t.get("season") in ("fall", "spring")), None)
               or next((t for t in dated if on(t)), None))
    season = picked if picked in ("fall", "spring", "summer", "winter") else None
    year = (re.search(r"\b(20\d{2})\b", text) or [None, None])[1] if season else None
    if season:
        matches = [t for t in dated if t.get("season") == season
                   and (not year or t.get("year") == int(year))]
        if not year:
            matches = [t for t in matches if spans[str(t["name"])][1] >= today]
        return matches[0] if matches else None
    if picked == "next":
        if current is None:
            return None
        later = [t for t in dated if spans[str(t["name"])][0] > spans[str(current["name"])][1]
                 and t.get("season") in ("fall", "spring")]
        return later[0] if later else None
    return current or next((t for t in dated if spans[str(t["name"])][0] >= today), None)


def entries_asked(message: str, kind: str, term: dict[str, Any],
                  dates: Sequence[dict[str, Any]], break_name: str | None = None,
                  ) -> list[dict[str, Any]]:
    """The term's entries of the kind asked, in date order; for a break the read names
    (`break_name`: "thanksgiving"), the break and holiday entries whose titles name it."""
    listed = [e for e in dates if term.get("name") in (e.get("terms") or [])]
    if kind == "break":
        found = [e for e in listed if e.get("kind") in ("break", "holiday")
                 and (not break_name or break_name in str(e.get("title")).casefold())]
    else:
        found = [e for e in listed if e.get("kind") == kind]
        if kind == "grading_option_deadline":
            # The option asked: an audit question is not answered by the last day to request
            # an incomplete, which is still weeks away when the audit deadline has passed.
            options = [title for title, words in _GRADING_OPTIONS if words.search(message)]
            chosen = [e for e in found
                      if any(o in str(e.get("title")).casefold() for o in options)]
            found = chosen if options else found
    return sorted(found, key=lambda e: (str(e.get("date")), str(e.get("title"))))


# Each grading option by the word its calendar titles use and the words a question asks it with.
_GRADING_OPTIONS = (("audit", re.compile(r"(?<!degree )\baudit", re.I)),
                    ("pass/fail", re.compile(r"\bpass\s*/?\s*(?:fail|no credit)\b|\bp\s*/\s*"
                                             r"(?:f|nc)\b", re.I)),
                    ("incomplete", re.compile(r"\bincomplete\b", re.I)))


# A break between two terms the calendar does not list by name: winter break is the weeks between
# the fall and spring semesters, summer break those between the spring and fall semesters.
_BETWEEN = {"winter": ("fall", "spring"), "summer": ("spring", "fall")}


Bounds = tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]


def between_terms(season: str, terms: Sequence[dict[str, Any]],
                  spans: dict[str, tuple[date, date]], dates: Sequence[dict[str, Any]],
                  today: date) -> Bounds | None:
    """The semesters around the next winter or summer break (the semester that begins after it
    has not begun yet) and the calendar's dates that bound it: the earlier semester's last day of
    classes, the first day of any classes between them (the winter session's), and the later
    semester's first day of classes. None when the calendar does not list both semesters."""
    ending_season, starting_season = _BETWEEN[season]
    dated = sorted((t for t in terms if t.get("name") in spans),
                   key=lambda t: spans[str(t["name"])][0])
    starting = next((t for t in dated if t.get("season") == starting_season
                     and spans[str(t["name"])][0] >= today), None)
    if starting is None:
        return None
    before = [t for t in dated if t.get("season") == ending_season
              and spans[str(t["name"])][1] <= spans[str(starting["name"])][0]]
    if not before:
        return None
    ending = before[-1]

    def of(term: dict[str, Any], kind: str) -> list[dict[str, Any]]:
        return sorted((e for e in dates if term["name"] in (e.get("terms") or [])
                       and e.get("kind") == kind), key=lambda e: str(e.get("date")))
    ends, begins = of(ending, "classes_end"), of(starting, "classes_begin")
    if not ends or not begins:
        return None
    last, first = ends[-1], begins[0]
    between = sorted((e for e in dates if e.get("kind") == "classes_begin"
                      and str(last["date"]) < str(e.get("date")) < str(first["date"])),
                     key=lambda e: (str(e.get("date")), str(e.get("title"))))
    return ending, starting, [last, *between, first]


def break_spans(calendar: Mapping[str, Any], today: date) -> list[tuple[str, date, date]]:
    """Every break the academic calendar dates, as (name, first day, last day): its break and
    holiday entries by their titles ("Thanksgiving Break"), and winter and summer break, which it
    does not list by name, as the days between the semesters around them (between_terms): from
    the day after the earlier one's last day of classes to the day before the later one's first.
    An event question names a break by these (events.window)."""
    terms, dates = calendar.get("terms") or [], calendar.get("dates") or []
    spans: list[tuple[str, date, date]] = []
    for entry in dates:
        if entry.get("kind") not in ("break", "holiday"):
            continue
        try:
            spans.append((str(entry.get("title") or ""), date.fromisoformat(str(entry["date"])),
                          last_day(entry)))
        except (KeyError, ValueError):
            continue
    term_days = term_spans(dates)
    for season in _BETWEEN:
        found = between_terms(season, terms, term_days, dates, today)
        if found is None:
            continue
        bounds = found[2]
        try:
            first = last_day(bounds[0]) + timedelta(days=1)
            last = date.fromisoformat(str(bounds[-1]["date"])) - timedelta(days=1)
        except (KeyError, ValueError):
            continue
        if first <= last:
            spans.append((f"{season.title()} Break", first, last))
    return spans


_MONTHS = {name: number for number, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"), start=1)}
# A question about a date already gone ("when was add/drop?"): what passed is what is asked.
_PAST = re.compile(r"\b(?:was|were|did)\b")
# "The next break" asks only what is still ahead ("next semester" names a term instead).
_NEXT_ONE = re.compile(r"\bnext\b(?!\s+(?:semester|term)\b)")


def last_day(entry: dict[str, Any]) -> date:
    """An entry's last day: its own day, or the last of the days it spans ("December 09, 12:00 am
    -- December 15, 11:59 pm" ends December 15)."""
    first = date.fromisoformat(str(entry["date"]))
    span = _DAY_SPAN.match(" ".join(str(entry.get("time") or "").split()))
    month = _MONTHS.get(span.group(1).casefold()) if span else None
    if span and month:
        try:
            return date(first.year + (1 if month < first.month else 0), month, int(span.group(2)))
        except ValueError:
            pass
    return first


# A calendar entry that says the college itself is closed that day ("Labor Day (College Closed -
# No Classes)"). "No Classes" alone is not a closure: offices keep their hours on a reading day.
_CLOSED = re.compile(r"\b(?:college|campus|offices?)\s+(?:is\s+|are\s+)?closed\b", re.I)
# Said before an office's hours on a day the calendar marks closed: the calendar's own title and
# page, never a conclusion about one office (its weekly hours stay as published).
CLOSED_TEXT = "Ramapo's [academic calendar]({url}) lists {day} as {title}."


def closed_on(calendar: Mapping[str, Any], day: date) -> str | None:
    """The sentence that the academic calendar marks `day` as a day the college is closed, with
    the entry's title and page; None when no entry with a page says so (2026-11-17 audit: asked
    about the library over Thanksgiving, the bot gave its weekly hours)."""
    for entry in calendar.get("dates") or []:
        title = " ".join(str(entry.get("title") or "").split())
        url = str(entry.get("source_url") or "")
        if not _CLOSED.search(title) or not url.startswith("https://"):
            continue
        try:
            first, last = date.fromisoformat(str(entry["date"])), last_day(entry)
        except (KeyError, ValueError):
            continue
        if first <= day <= last:
            return CLOSED_TEXT.format(url=url, day=_day(day.isoformat()), title=literal(title))
    return None


def dates_said(message: str, kind: str, calendar: dict[str, Any], today: date,
               n: int = 1, asked: AskedTime | None = None,
               ) -> tuple[str, str, dict[str, Any]] | None:
    """Code's answer to when one kind of academic date is, from the calendar's own entries: its
    text, the calendar page it cites as [n], and the entries it says (what its text is checked
    against). None when no term or entry fits, and the model reads the calendar pages.

    Dates start from today (11-03: "wen is spring brk" got March 2028 from a housing page):
    - a date already over is marked as passed, and left out when the next one is asked;
    - a question naming no term, about a date to come, gets the term in session, or the next one
      that lists that kind of date still ahead when the term in session lists none;
    - winter and summer break, which the calendar does not list by name, are said by the dates
      around them: the earlier semester's last day, the winter session's first, the later
      semester's first;
    - a break the term does not list ("fall break") is said not to be listed, beside the days with
      no classes it does list.

    `asked` is the read's picks (asked_time.py), None when there was no read: whether a term is
    named and which, and which break."""
    terms, dates = calendar["terms"], calendar["dates"]
    spans = term_spans(dates)
    text = message.casefold()
    term_pick = asked.term if asked is not None else None
    named_term = term_pick not in (None, "none")
    break_name = (asked.break_name if asked is not None and asked.break_name != "none"
                  else None)
    # "Can I still ...": this term's date is the answer, passed or not, not the next term's.
    still = bool(_STILL.search(text.replace("’", "'")))
    season = break_name if kind == "break" else None
    if season in _BETWEEN and not named_term:
        found = between_terms(season, terms, spans, dates, today)
        if found is None:
            return None
        ending, starting, entries = found
        url = _url_of([ending, starting], entries)
        if url is None:
            return None
        lines = _lines(entries, today)
        said = "\n".join([f"The academic calendar doesn't list a {season} break by name. "
                          f"Between the {literal(ending['name'])} and "
                          f"{literal(starting['name'])} semesters it lists [{n}]:", *lines])
        return said, url, {"terms": [ending, starting], "entries": entries}
    term = term_asked(message, terms, spans, today, term_pick)
    if term is None:
        return None
    entries = entries_asked(message, kind, term, dates, break_name)
    if (not named_term and not still and not _PAST.search(text)
            and all(last_day(e) < today for e in entries)):
        # Nothing of that kind still ahead in this term: the next term that lists one is asked.
        later = sorted((t for t in terms if t.get("name") in spans
                        and spans[str(t["name"])][0] > spans[str(term["name"])][0]),
                       key=lambda t: spans[str(t["name"])][0])
        for other in later:
            ahead = entries_asked(message, kind, other, dates, break_name)
            if any(last_day(e) >= today for e in ahead):
                term, entries = other, ahead
                break
    if _NEXT_ONE.search(text) and any(last_day(e) >= today for e in entries):
        entries = [e for e in entries if last_day(e) >= today]
    if not entries and season is not None:
        # A break the term does not list: said so, with the days with no classes it lists.
        entries = [e for e in entries_asked("", "break", term, dates) if last_day(e) >= today]
        url = _url_of([term], entries)
        if url is None:
            return None
        lead = (f"The {literal(term['name'])} academic calendar doesn't list a {season} break "
                f"[{n}].")
        said = "\n".join([f"{lead} The days with no classes it lists are:",
                          *_lines(entries, today, str(term["name"]))]) if entries else lead
        return said, url, {"term": term, "entries": entries}
    url = _url_of([term], entries)
    listed = calendar_text(term, entries, n, today)
    if url is None or listed is None:
        return None
    return listed, url, {"term": term, "entries": entries}


def _url_of(terms: Sequence[dict[str, Any]], entries: Sequence[dict[str, Any]]) -> str | None:
    return next((str(url) for url in [*(t.get("source_url") for t in terms),
                                      *(e.get("source_url") for e in entries)] if url), None)


def _lines(entries: Sequence[dict[str, Any]], today: date | None,
           term: str | None = None) -> list[str]:
    """Each entry with its day, title and time, whether it has passed, and the other term it is
    also listed for: the Fall 2026 calendar lists the winter session's first dates, and "Last Day
    to Request Pass/Fail and Audit Grade Option" on December 18 is the winter session's, though
    it reads as the fall's (fix check 11-04)."""
    lines = []
    for e in entries:
        others = [str(t) for t in e.get("terms") or [] if term is not None and str(t) != term]
        lines.append(f"- {_day(str(e['date']))}: {literal(e['title'])}{_time(e.get('time'))}"
                     + (f" (also on the {literal(' and '.join(others))} calendar)"
                        if others else "")
                     + (" (already passed)" if today is not None and last_day(e) < today
                        else ""))
    return lines


def _day(value: str) -> str:
    day = date.fromisoformat(value)
    return f"{day:%A, %B} {day.day}, {day.year}"


# An entry's time as the calendar publishes it: a whole day ("12:00 am - 11:59 pm") says nothing
# its day does not, and a span of days ("December 09, 12:00 am -- December 15, 11:59 pm") is
# said by its last day.
_WHOLE_DAY = re.compile(r"^12:00 am(?:\s*-\s*11:59 pm)?$", re.I)
_DAY_SPAN = re.compile(r"^\w+ \d{1,2}, 12:00 am\s*--\s*(\w+) 0?(\d{1,2}), 11:59 pm$", re.I)


def _time(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text or _WHOLE_DAY.match(text):
        return ""
    if span := _DAY_SPAN.match(text):
        return f" (through {span.group(1)} {span.group(2)})"
    return f" ({literal(text)})"


def calendar_text(term: dict[str, Any], entries: Sequence[dict[str, Any]],
                  n: int = 1, today: date | None = None) -> str | None:
    """Code's answer: the entries asked, each with its day, title and time as the term's academic
    calendar lists them [n], and whether it has already passed (by `today`). None when there are
    none (the model reads the pages then)."""
    if not entries:
        return None
    return "\n".join([f"The {literal(term['name'])} academic calendar lists [{n}]:",
                      *_lines(entries, today, str(term["name"]))])
