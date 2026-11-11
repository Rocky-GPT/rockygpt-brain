"""Academic calendar dates: which term and which kind of date a question asks about, and code's
answer from the calendar's own entries.

Ramapo's academic calendar pages list each term's dates ("Fall 2026": classes begin, the add/drop
and withdrawal deadlines, breaks, final exams), each entry with its title and day as published and
the kind of date it is (rockygpt-data calendar-concepts.ts). A question asking only when one kind
of date is ("when are finals?", "last day to withdraw this semester", "when does the spring
semester start?") is answered by code: every entry of that kind in the term asked, in date order,
with no model call. Anything else, and a kind the term lists no entry of, is left to the model,
which reads the calendar pages.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from rockygpt_brain.plain_writer import literal

# The kind of date a question asks about, by its words; a question naming two kinds is the model's.
_KINDS = (
    ("add_drop_deadline", re.compile(r"\badd\s*(?:/|and|or)?\s*drop\b|\bdrop (?:a |my )?class"
                                     r"(?:es)?\b|\blast day to (?:add|drop)\b|"
                                     r"\badd(?:ing)? (?:a |an |another |one more |my )?"
                                     r"(?:new )?(?:class|course)(?:es|s)?\b")),
    ("withdrawal_deadline", re.compile(r"\bwithdraw\w*\b")),
    ("tuition_refund_deadline", re.compile(r"\brefunds?\b")),
    # Pass/fail, audit and incomplete: the calendar's grading option deadline ("can I still switch
    # a class to audit" was told the deadline could not be checked, fix check 11-04); a degree
    # audit is no grading option.
    ("grading_option_deadline", re.compile(r"(?<!degree )\baudit(?:ing|ed)?\b|\bpass\s*/?\s*"
                                           r"(?:fail|no credit)\b|\bp\s*/\s*(?:f|nc)\b|"
                                           r"\bgrading options?\b|\bincomplete\b")),
    # "Final" alone is the exams only when no other thing follows it: "my final grade" asks no
    # exam dates (fix check 11-04: a grade appeal began with the finals schedule).
    ("finals", re.compile(r"\bfinals\b|\bfinal exam\w*\b|\bexam week\b|\bfinal\b(?!\s+(?:grades?|"
                          r"projects?|papers?|essays?|decisions?|determinations?|days?|steps?|"
                          r"versions?|drafts?|answers?|say|word|year|semester|term|round|"
                          r"transcripts?|averages?|scores?|gpa|presentations?|portfolios?))")),
    ("grades_due", re.compile(r"\bgrades? (?:are )?due\b")),
    ("break", re.compile(r"\bbreak\b|\bthanksgiving\b|\brecess\b")),
    ("classes_begin", re.compile(r"\b(?:classes|semester|term) (?:start|starts|begin|begins)\b|"
                                 r"\bfirst day of (?:classes|the semester)\b|"
                                 r"\bstart of (?:the )?(?:semester|term)\b")),
    ("classes_end", re.compile(r"\b(?:classes|semester|term) (?:end|ends|finish|finishes|over)\b|"
                               r"\blast day of (?:classes|the semester)\b|"
                               r"\bend of (?:the )?(?:semester|term)\b")),
)
# A break the question names: its entries are the ones whose titles name it.
_BREAK_NAMES = ("thanksgiving", "spring", "winter", "fall", "summer")
# "Spring break" names a break, not the spring term: winter break is no part of the winter session.
_TERM = re.compile(r"\b(winter|spring|summer|fall|autumn)(?!\s+(?:break|recess)\b)"
                   r"(?:\s+(?:semester|term|session))?(?:\s+(20\d{2}))?\b")
_NEXT = re.compile(r"\bnext (?:semester|term)\b")
# Words of a plain calendar question besides its kind and term words; any other word means it
# asks more ("if I'm a transfer student"), and the model reads it.
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
# A question about the term in session ("this semester") asks about it, even when its dates of
# that kind have all passed.
_THIS_TERM = re.compile(r"\b(?:this|current|the current)\s+(?:semester|term)\b")


def kind_asked(message: str) -> str | None:
    """The one kind of calendar date a question asks when it is, or None."""
    text = message.casefold().replace("’", "'")
    if not re.search(r"\bwhen\b|\bwhat (?:day|date)\b|\blast day\b|\bdeadline\b", text):
        return None
    found = [kind for kind, pattern in _KINDS if pattern.search(text)]
    words = re.findall(r"[a-z0-9]+", text.replace("'", ""))
    if len(found) != 1 or any(w not in _PLAIN and not re.fullmatch(r"20\d{2}", w)
                              for w in words):
        return None
    return found[0]


def kind_named(message: str) -> str | None:
    """The one kind of calendar date a question names when it asks when or a deadline, whatever
    else it asks ("what's the deadline to withdraw and what shows on my transcript"): code says
    those dates first and the model the rest (11-03: the reply gave a page's 7-week dates and left
    out the calendar's full-semester Nov 6 deadline). None for no kind or two."""
    text = message.casefold().replace("’", "'")
    if not (re.search(r"\bwhen\b|\bwhat (?:day|date)\b|\blast day\b|\bdeadlines?\b|"
                      r"\bdue date\b|\bby when\b|\bhow late\b|\buntil when\b", text)
            or _STILL.search(text)):
        return None
    found = [kind for kind, pattern in _KINDS if pattern.search(text)]
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
               spans: dict[str, tuple[date, date]], today: date) -> dict[str, Any] | None:
    """The term a question asks about: one it names ("spring", "fall 2026"; a season alone is its
    term in session or the next to start), the fall or spring after the current one ("next
    semester"), else the fall or spring in session today, else the next term to start."""
    text = message.casefold()
    dated = sorted((t for t in terms if t.get("name") in spans),
                   key=lambda t: spans[str(t["name"])][0])

    def on(term: dict[str, Any]) -> bool:
        first, last = spans[str(term["name"])]
        return first <= today <= last
    current = (next((t for t in dated if on(t) and t.get("season") in ("fall", "spring")), None)
               or next((t for t in dated if on(t)), None))
    named = _TERM.search(text)
    if named:
        season = "fall" if named.group(1) == "autumn" else named.group(1)
        matches = [t for t in dated if t.get("season") == season
                   and (not named.group(2) or t.get("year") == int(named.group(2)))]
        if not named.group(2):
            matches = [t for t in matches if spans[str(t["name"])][1] >= today]
        return matches[0] if matches else None
    if _NEXT.search(text):
        if current is None:
            return None
        later = [t for t in dated if spans[str(t["name"])][0] > spans[str(current["name"])][1]
                 and t.get("season") in ("fall", "spring")]
        return later[0] if later else None
    return current or next((t for t in dated if spans[str(t["name"])][0] >= today), None)


def entries_asked(message: str, kind: str, term: dict[str, Any],
                  dates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The term's entries of the kind asked, in date order; for a break the question names
    ("Thanksgiving break"), the break and holiday entries whose titles name it."""
    listed = [e for e in dates if term.get("name") in (e.get("terms") or [])]
    if kind == "break":
        text = message.casefold()
        named = [w for w in _BREAK_NAMES if re.search(rf"\b{w}\b", text)
                 and not re.search(rf"\b{w}\s+(?:semester|term|20\d\d)\b", text)]
        found = [e for e in listed if e.get("kind") in ("break", "holiday")
                 and (not named or any(w in str(e.get("title")).casefold() for w in named))]
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


def break_named(message: str) -> str | None:
    """The one break a question names by its season or holiday ("winter break", "Thanksgiving"),
    or None: a season named as a term ("spring semester") names no break."""
    text = message.casefold()
    named = [w for w in _BREAK_NAMES if re.search(rf"\b{w}\b", text)
             and not re.search(rf"\b{w}\s+(?:semester|term|session|20\d\d)\b", text)]
    return named[0] if len(named) == 1 else None


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


def dates_said(message: str, kind: str, calendar: dict[str, Any], today: date,
               n: int = 1) -> tuple[str, str, dict[str, Any]] | None:
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
      no classes it does list."""
    terms, dates = calendar["terms"], calendar["dates"]
    spans = term_spans(dates)
    text = message.casefold()
    named_term = bool(_TERM.search(text) or _NEXT.search(text) or _THIS_TERM.search(text))
    # "Can I still ...": this term's date is the answer, passed or not, not the next term's.
    still = bool(_STILL.search(text.replace("’", "'")))
    season = break_named(message) if kind == "break" else None
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
    term = term_asked(message, terms, spans, today)
    if term is None:
        return None
    entries = entries_asked(message, kind, term, dates)
    if (not named_term and not still and not _PAST.search(text)
            and all(last_day(e) < today for e in entries)):
        # Nothing of that kind still ahead in this term: the next term that lists one is asked.
        later = sorted((t for t in terms if t.get("name") in spans
                        and spans[str(t["name"])][0] > spans[str(term["name"])][0]),
                       key=lambda t: spans[str(t["name"])][0])
        for other in later:
            ahead = entries_asked(message, kind, other, dates)
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
