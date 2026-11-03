"""One model call for a how-to or policy question.

Four calls in a row (plan, pick sections, finish, write) took 28 s a question. Code now finds the
evidence: each part of the question is searched by meaning, the best units of each part are read
exactly as published (an entry's steps with the page's words, a section's text), grouped by an
office that links them, with those offices' contacts. One call then lists the parts asked and
the sections that answer each, and writes the reply from them (a list of each part's facts as
well was longer than the reply and said it twice; dropped 10-21). On the same 93
untouched blind questions it matched the four-call answers (69% vs 72%, 0 serious) at 9 s and
$0.0010 a question (10-21). Offices' contacts and hours, menus and the shuttle keep their own
lookups: a question about them is not routed here.
"""

import json
import re
from collections.abc import Sequence
from datetime import date
from typing import Any

from rockygpt_brain.academics import asks_academics
from rockygpt_brain.events import (
    asks_club_events,
    asks_club_exists,
    asks_clubs,
    asks_events,
    plain_window,
)
from rockygpt_brain.people import about_named, asks_leadership, asks_people, only_who
from rockygpt_brain.retrieval.meaning import MeaningIndex, MeaningScores
from rockygpt_brain.writer import cited

PER_PART = 10  # Each part's own best units: a "how do I apply" part keeps its application section.
PER_PAGE = 2  # At most this many from one page, so its near-copies don't crowd out the next page.
MAX_UNITS = 24
MAX_PARTS = 3
SECTION_WORDS_CHARS = 3200

ONE_CALL_PROMPT = """You answer a Ramapo College student from the college's own published pages,
given as JSON. You know nothing beyond the JSON and cannot look anything up.
- First decide which sections answer what the student asked; ignore the rest. A section for
  another group, program, place or term does not answer them; past_term means it is for a term or
  deadline that is over: say so and never give its dates as coming up. Never change a year,
  class year or term to make a date current.
- Answer what was asked directly and completely: every step, form, link, email, phone, date,
  deadline, amount and condition the chosen sections give for it, exactly as written (a step's
  page words in quotes are the page's own). Never add, compute or guess anything the JSON does not
  say.
- A weekly timetable a section lists (sessions by course and hour, each staff member's hours) is
  given only when the student asks when or what times. Asked where or how to get help, name the
  place, how to book or join and who to contact, and say its page lists the times, with its mark.
- When sections disagree about the same date, amount or rule for the same people, place and term,
  the newer page wins: when their page_updated dates differ, give the value of the section whose
  page_updated is later, with its mark, then say in one short sentence that an older page (named by
  office or title, with its page_updated month and year) says otherwise. When page_updated is
  missing or the same, give each with its mark, naming its page, and say the pages differ; never
  pick one silently. Different values for different groups, places or terms are not a
  disagreement: say which applies to whom. page_updated is the day a section's page was last
  changed.
- Name the office before its steps and contacts. Give contacts only of offices whose sections you
  used, or the right office when no section answers.
- Answer every part of the question. Before saying a part is not covered, check every section
  given, including its section_words. For a part no section answers, say once that you don't have
  verified information about it, and point to the office most likely to help with its contacts.
- computed are facts code worked out from the catalog, the directory, the calendar and the
  dates the sections state: state every one that answers the question, exactly as given (names,
  titles, counts, dates, numbers), with its number when it has one, and do what one says (ask
  which person).
  They override any page section: a page that disagrees with them is out of date, and is not
  repeated.
- A program's requirements (programs.requirements) were written out by code from the catalog: say
  them as given, every course and how many of a list to take, with their notes; never add up
  credits or courses yourself. Name the program and its degree; when also_published lists its
  other levels, name them in one short line. A course (programs entries with "course") is said
  with its credits, prerequisites (every alternative and condition, in the catalog's words,
  never shortened) and attributes as given. A graduation_plan is the catalog's
  recommended sequence for the admission year it names: say which year it is for, and give its
  semesters and totals as written; other_plans_same_year names that year's other tracks.
  catalog_programs lists every published program by level: a major, minor or program it does
  not list is not offered in the catalog; say so plainly.
- calendar.events are the college calendar's events in the window code chose (calendar.window):
  give each one's title, day and time, place and host as written, with its number. When
  calendar.said_by_code is true, already_written already gives these events and says whether any
  match what was asked: never list, restate or count them again; say only what else is asked
  about them. When
  calendar.topic_matched is false, first say the calendar lists nothing for what was asked in
  that window, then give what the sections say about it (a fair's season, a series' next
  date), then offer the window's other events; with no events, say so and give
  calendar_url. When topic_matched is true the events are those about the topic, among
  calendar.events_on_the_calendar_in_it: never call one the only event in the window. An event
  marked outside_window is on a later date than the one asked: say so with its date. tickets
  FREE means free admission. clubs are directory entries:
  give each club's name, category and page as written; mission and member_benefits are the
  club's own words about what it does. Never add events, times or clubs.
- people are directory and faculty-profile entries: give the asked facts (email, phone, office
  room, title, school, office hours as written, courses, interests) exactly as given, with the
  person's number. If several share the name asked, list each with title and school and ask which.
  A person marked near_spelling_of_asked_name is not the name asked: say no one by that exact
  name is listed and ask whether they mean this person, giving their name, title and school.
  Who to contact about a person's department is their school's office among the offices.
  A person's email, phone, office room and office hours come from their people entry, never
  from page text. A list question is answered with every person given, each with the fields
  asked; when the first carries listed_first_of, say these are the first of that many.
  If the named person is not among them, say the directory lists no one by that name. Never guess
  a person, email or room.
- already_written is the start of the reply, written by code from the college's own records
  and placed before your text exactly as given. Never repeat, restate, shorten or contradict it:
  it is the answer to what it covers, and a page that disagrees with it is out of date. Write only
  what else the question asks. When it already answers everything asked, write an empty text.
- When contacts_written_by_code is true, code adds the phone numbers, email and room of each
  office whose sections you cite after your text: name the office and give its steps, but do not
  list its phone numbers, email or room yourself, except a number a step's own words give.
- Write a phone number as (XXX) XXX-XXXX with its own digits.
- Cite each statement with its section number like [3]. Numbers are only marks: never write
  "section 4" in the text. Short plain sentences; numbered steps are fine. Under 1,600
  characters. No greeting.
Work in this order: list each part the student asked (parts.asked); for each, whether it asks for
something no college page holds: the student's own records or status, something live right now,
or an opinion (parts.not_on_pages), and the numbers of every section that answers it, read in full
with its section_words (parts.sections; none when no section does). Then write the reply (text)
from those sections. Answer only with the write_answer tool."""

ONE_CALL_TOOL: dict[str, Any] = {
    "type": "function", "name": "write_answer", "strict": True,
    "description": "The parts asked, the sections that answer each, then the reply.",
    "parameters": {"type": "object", "additionalProperties": False,
                   "required": ["parts", "text"],
                   "properties": {
                       "parts": {"type": "array", "items": {
                           "type": "object", "additionalProperties": False,
                           "required": ["asked", "not_on_pages", "sections"],
                           "properties": {
                               "asked": {"type": "string"},
                               "not_on_pages": {"type": "boolean"},
                               "sections": {"type": "array", "items": {"type": "integer"}}}}},
                       "text": {"type": "string", "maxLength": 8000}}},
}

# Questions with their own code-computed answers (menus, dining hours, the shuttle) and plain
# contact or hours questions keep the lookups that read them; everything else is a how-to.
_SERVICES = re.compile(
    r"\b(menu|menus|eat|food|lunch|dinner|breakfast|brunch|vegan|vegetarian|allerg\w*|gluten|"
    r"dining hall|cafe|caf|atrium|dunkin|starbucks|common grounds|birch tree|shuttle|bus|buses|"
    r"train|route|stop)\b")
_MEAL_PLAN = re.compile(r"\bmeal ?plans?\b|\bflex\b|\bdining dollars\b")
_HOURS = re.compile(r"\b(hours|open|opens|close|closes|closing|closed)\b")
_CONTACT = re.compile(r"\b(email|e-mail|phone|number|call|fax|where is|located|location|room|"
                      r"office)\b")
_HOWTO = re.compile(
    r"\b(how (do|can|would|should|to)|can i|could i|may i|am i|allowed|policy|policies|rules?|"
    r"deadline|due|what happens|what if|appeal|apply|application|request|refund|fee|fees|fine|"
    r"form|process|steps?|requirements?|eligib\w*|register|registration|drop|withdraw\w*|"
    r"transfer|declare|change|replace|lost|report|reserve|book|get|need|happens)\b")


# "What is the Birch Tree Inn?", "what does OSS do?": what a place or an office is, which only its
# own pages say. The services and contact lookups read hours, menus and contacts, never that (11-02:
# "The name is Birch Tree Inn."), so it is read here unless the question also asks one of those.
_WHAT_IS = re.compile(r"^\W*(?:what(?:'s|’s| is| are)\b|what does\b.+\bdo\b|tell me about\b)")
_A_DETAIL = re.compile(
    r"\b(menus?|eat|food|lunch|dinner|breakfast|brunch|vegan|vegetarian|allerg\w*|gluten|"
    r"serve[sd]?|serving|shuttle|bus|buses|train|route|stop|hours|open|opens|close|closes|"
    r"closing|closed|time|email|e-mail|phone|number|call|fax|where|located|location|room|"
    r"address|today|tonight|tomorrow|now)\b")


# "Where's" is "where is": the read writes its clean question in everyday English, and a place
# asked about that way goes to the lookups like "where is" does (11-03 check set: "Where's the
# Bradley Center?" went to the how-to pages, which have no section saying where it is).
_WHERE_IS = re.compile(r"\bwhere(?:'|’)?s\b")


def routes_here(message: str) -> bool:
    """Whether a message is a how-to, policy or what-is question for the one-call path."""
    text = _WHERE_IS.sub("where is", message.casefold())
    if _WHAT_IS.search(text) and not _A_DETAIL.search(text):
        return True
    if _SERVICES.search(text) and not _MEAL_PLAN.search(text) and not asks_academics(text) \
            and not asks_events(text) and not asks_clubs(text) and not asks_people(text):
        return False
    howto = _HOWTO.search(text)
    if (_HOURS.search(text) or _CONTACT.search(text)) and not howto:
        return False
    return True


# Questions code answers alone from the facts it works out, the model writing nothing (Dan 11-02):
# who leads a school or field, one named person's details, one catalog course's facts.
_WHO = re.compile(r"^\W*(?:who|who's|whos)\b")
_PERSON_ASK = re.compile(r"^\W*(?:what|what's|whats|where|when)\b")
_PERSON_FIELD = re.compile(
    r"\b(?:e-?mail|phone|number|office|room|hours|title|department|school|teach\w*|courses?|"
    r"class(?:es)?|research\w*|interests?)\b")
_COURSE_ASK = re.compile(r"^\W*(?:what|what's|whats|how many|does|is|are)\b")
_COURSE_WHAT = re.compile(r"^\W*(?:what(?:'s|’s|s| is)|tell me about|describe)\b")
_COURSE_FACT = re.compile(r"\b(?:credits?|prerequisites?|prereqs?|requisites?|gen ?ed)\b")
# "...and how do I reach them?" after a leadership question asks for the leader's contacts.
_REACH = re.compile(r"\band (?:how (?:do|can|would|should) i (?:reach|contact|email|call|get in "
                    r"touch with) (?:them|him|her|the dean|the chair)|what(?:'s| is| are) "
                    r"(?:their|his|her) (?:e-?mail|phone(?: number)?|number|office|contact"
                    r"(?: info(?:rmation)?)?))\W*$")


def asks_reach(question: str) -> bool:
    return bool(_REACH.search(question.casefold()))


# A how or why asks for more than the facts, as does a second clause ("... and is it hard"); an
# "and" inside a name does not ("School of Social Science and Human Services").
_MORE = re.compile(r"\b(?:how do|how can|why|should|can i|could i)\b|\b(?:and|also|plus)\s+"
                   r"(?:what|how|who|when|where|can|do|does|will|is|are|should)\b")
# A course fact by the words that ask for it: only those facts are said.
_COURSE_WANTS = ((re.compile(r"\bcredits?\b"), " credit"),
                 (re.compile(r"\b(?:prerequisites?|prereqs?|requisites?)\b"), "prerequisite"),
                 (re.compile(r"\bgen ?ed\b"), "Gen Ed"))


def code_kind(question: str, named_people: Sequence[Any],
              programs: tuple[Sequence[Any], Sequence[Any], Sequence[Any]] | None) -> str | None:
    """Which code-answered question this is, or None when the model should write it."""
    text = question.casefold()
    # Who leads a school, and how to reach them: the dean's directory contacts are code's too.
    if ((reach := _REACH.search(text)) and _WHO.search(text)
            and asks_leadership(about_named(question, named_people))
            and not _MORE.search(text[:reach.start()])):
        return "leaders"
    # Only what is on in a window ("what can I do this weekend"): its every word is a plain one.
    if not named_people and not (programs and any(programs)) and plain_window(question):
        return "events"
    if _MORE.search(text):
        return None
    # Whether a club exists, or what it hosts next: code checks the name it gives.
    if asks_clubs(text) and (asks_club_events(text) or asks_club_exists(question)):
        return "club"
    # One person named and nothing else asked comes first: "Who is Dean Chen?" asks about
    # Professor Dean Chen, not about deans.
    if len(named_people) == 1 and (
            (_PERSON_ASK.search(text) and _PERSON_FIELD.search(text))
            or (_WHO.search(text) and only_who(question, named_people[0]))):
        return "person"
    if asks_leadership(about_named(question, named_people)) and _WHO.search(text):
        return "leaders"
    if programs and not programs[0] and len(programs[2]) == 1 and (
            (_COURSE_ASK.search(text) and _COURSE_FACT.search(text)) or _COURSE_WHAT.search(text)):
        return "course"
    return None


def asks_more(question: str) -> bool:
    """Whether a question asks something besides what code lists ("and how do I", "can I")."""
    return bool(_MORE.search(question.casefold()))


def course_facts_asked(question: str, facts: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The course facts a question asks for ("how many credits" is the credits line), or all."""
    text = question.casefold()
    wanted = [word for pattern, word in _COURSE_WANTS if pattern.search(text)]
    kept = [f for f in facts if any(w in str(f.get("fact", "")) for w in wanted)]
    return kept or list(facts)


def code_text(facts: Sequence[dict[str, Any]]) -> str | None:
    """The facts code worked out, said as they are with their marks; None when one of them is
    written for a writer rather than a student ("Ask which one the student means")."""
    lines = [f"{fact['fact']} [{fact['n']}]" if fact.get("n") else str(fact["fact"])
             for fact in facts if isinstance(fact, dict) and fact.get("fact")]
    if not lines or any("the student" in line for line in lines):
        return None
    return " ".join(lines)


def parts_of(question: str) -> list[str]:
    """The texts searched: the whole question, then its sentences and its clauses joined by "and
    what/how/who/...", up to MAX_PARTS of them."""
    bits = [b.strip() for b in re.split(
        r"(?<=[?.!])\s+|\s+(?:and|also|plus)\s+(?=(?:what|how|who|when|where|can|do|does|will|is|"
        r"are|should)\b)", question, flags=re.IGNORECASE) if len(b.strip()) > 12]
    return [question, *[b for b in bits if b != question][:MAX_PARTS]]


def search_text(latest: str, earlier_user: str | None) -> str:
    """A short follow-up ("and for grad students?") is searched with the question before it."""
    if earlier_user and len(latest.split()) <= 6:
        return f"{earlier_user}\n{latest}"
    return latest


def chosen_units(index: MeaningIndex, vectors: Sequence[Sequence[float]], cap: int = MAX_UNITS,
                 seen: set[tuple[str, str]] | None = None, *, per_part: int = PER_PART,
                 per_page: int = PER_PAGE) -> tuple[MeaningScores, list[tuple[str, str]]]:
    """Each part's own best units (at most PER_PAGE from one page record), interleaved so every
    part keeps its first few, at most `cap`; their scores and match offsets. Units in `seen` were
    already given and are skipped; the chosen ones are added to it."""
    seen = set() if seen is None else seen
    keyed = {(u.record_id, u.index): u for u in index.units}
    chosen: dict[tuple[str, str], float] = {}
    at: dict[tuple[str, str], int] = {}
    by_part: list[list[tuple[str, str]]] = []
    for vector in vectors:
        raw = index.scores(vector)
        best: list[tuple[str, str]] = []
        on_page: dict[str, int] = {}
        for k, _ in sorted(raw.items(), key=lambda kv: -kv[1]):
            if k not in keyed or k in seen or on_page.get(k[0], 0) >= per_page:
                continue
            on_page[k[0]] = on_page.get(k[0], 0) + 1
            best.append(k)
            if len(best) >= per_part:
                break
        by_part.append(best)
        for k in best:
            chosen[k] = max(chosen.get(k, -9.0), raw[k])
            if k in raw.at:
                at[k] = raw.at[k]
    order: list[tuple[str, str]] = []
    for n in range(per_part):
        for best in by_part:
            if n < len(best) and best[n] not in order:
                order.append(best[n])
    order = order[:cap]
    seen.update(order)
    scores = MeaningScores({k: chosen[k] for k in order})
    scores.at = {k: at[k] for k in order if k in at}
    return scores, order


# Before reading, the call can pick from a wider pool by heading: the section that answers is
# often ranked 11th to 60th by meaning, behind near-misses (on 37 graded misses of sets zf and zg
# the answering page was read for 35 instead of 31, at ~1.4 s and $0.0001 a question, 10-21).
POOL, POOL_PER_PART, POOL_PER_PAGE, PICKED = 60, 30, 3, 12
# With a pick, only 12 units are read: on 7 zi misses whose fact was in the 24 units read, the reply
# held it 11 of 14 times from 12 units and 9 of 14 from 24, from half the text (10-21).
PICKED_READ = 12
PICK_PROMPT = (
    "A Ramapo College student asked a question. Below are numbered sections of the college's "
    "pages, each by its page address and heading. Pick the sections whose full text most likely "
    "answers what the student asked (every part of it), best first, at most 12. Answer only with "
    "the pick_units tool.")
PICK_TOOL: dict[str, Any] = {
    "type": "function", "name": "pick_units", "strict": True,
    "description": "The sections to read, best first.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["n"],
                   "properties": {"n": {"type": "array", "items": {"type": "integer"}}}},
}


def pick_messages(question: str, index: MeaningIndex,
                  pool: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """The question and the pool's sections by page address and heading, numbered from 1."""
    lines = [f"{n}. {' | '.join(index.labels.get(key, ('', '')))}"
             for n, key in enumerate(pool, start=1)]
    return [{"role": "system", "content": PICK_PROMPT},
            {"role": "user", "content": f"Question: {question}\n\n" + "\n".join(lines)}]


def with_picked(index: MeaningIndex, vectors: Sequence[Sequence[float]],
                pool_scores: MeaningScores, pool: Sequence[tuple[str, str]],
                arguments: dict[str, Any] | None) -> tuple[MeaningScores, list[tuple[str, str]]]:
    """The picked sections first, then the usual best of each part, at most PICKED_READ; the
    usual choice alone when nothing valid was picked."""
    scores, order = chosen_units(index, vectors)
    picked = [pool[n - 1] for n in (arguments or {}).get("n") or []
              if isinstance(n, int) and 1 <= n <= len(pool)][:PICKED]
    if not picked:
        return scores, order
    final = list(dict.fromkeys([*picked, *order]))[:PICKED_READ]
    merged = MeaningScores({k: pool_scores.get(k, scores.get(k, 0.0)) for k in final})
    merged.at = {k: at for k in final
                 if (at := pool_scores.at.get(k, scores.at.get(k))) is not None}
    return merged, final


CARD_OFFICES = 2  # Offices found by their cards, read for their contacts.


def card_offices(index: MeaningIndex, vectors: Sequence[Sequence[float]],
                 limit: int = CARD_OFFICES) -> list[str]:
    """The offices whose cards (name, other names, contact titles) best match any part: who
    handles it, even an office with no how-to sections."""
    best: dict[str, float] = {}
    for vector in vectors:
        for entity_id, score in index.office_scores(vector)[:limit]:
            best[entity_id] = max(best.get(entity_id, -2.0), score)
    return sorted(best, key=best.__getitem__, reverse=True)[:limit]


def gather(facts: Any, index: MeaningIndex, vectors: Sequence[Sequence[float]], question: str,
           dataset_version: str, identity_hash: str | None,
           named_offices: Sequence[str] = (), *, cap: int = MAX_UNITS,
           numbered_from: int = 0, seen: set[tuple[str, str]] | None = None,
           chosen: tuple[MeaningScores, list[tuple[str, str]]] | None = None,
           ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The evidence the call reads, by office (contacts and numbered sections), and the sources
    the numbers stand for. Offices the student named are read for their contacts too. A second
    look numbers its sections after the first look's (`numbered_from`) and skips the units it
    gave (`seen`)."""
    keyed = {(u.record_id, u.index): u for u in index.units}
    if chosen is None:
        scores, order = chosen_units(index, vectors, cap, seen)
    else:
        scores, order = chosen
        if seen is not None:
            seen.update(order)
    groups: dict[str, list[tuple[str, str]]] = {}
    for key in order:
        groups.setdefault(keyed[key].entity_ids[0], []).append(key)
    for entity_id in named_offices:
        groups.setdefault(entity_id, [])
    offices: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for entity_id, keys in groups.items():
        only = MeaningScores({k: scores[k] for k in keys})
        only.at = {k: scores.at[k] for k in keys if k in scores.at}
        fields = ["email", "phones", "offices", *(["procedures"] if keys else [])]
        found = facts.get_office_facts(entity_id, fields, dataset_version,
                                       identity_hash=identity_hash, about=question,
                                       meaning=only if keys else None)
        props = {p["key"]: p for p in found["properties"]}

        def values(key: str, props: dict[str, Any] = props) -> list[Any]:
            prop = props.get(key) or {}
            return ([v["value"] for v in prop.get("values", [])]
                    if prop.get("status") == "known" else [])
        sections = []
        for value in values("procedures"):
            sources.append({"n": numbered_from + len(sources) + 1, "url": value.get("page"),
                            "title": value.get("heading")})
            sections.append({
                "n": numbered_from + len(sources), "heading": value.get("heading"),
                "page": value.get("page"),
                "text": value.get("text"),
                **({"section_words": value["section_words"][:SECTION_WORDS_CHARS]}
                   if value.get("section_words") else {}),
                **{k: value[k] for k in ("applies_to", "handled_by", "term", "past_term",
                                          "page_updated")
                   if value.get(k)}})
        offices.append({"office": (found.get("entity") or {}).get("name") or entity_id,
                        "email": values("email"), "phones": values("phones"),
                        "rooms": values("offices"), "sections": sections})
    return offices, sources


SECOND_LOOK_UNITS = 12
_GAVE_UP = re.compile(r"don.t have verified")


# A reply that says the pages are silent on something it was asked ("The pages don't say how to
# check for a hold"): on the graded misses of sets zi-zk (10-21) that was the most common miss,
# 20 of 45, and in most of them another page did say it. The sentence itself is searched.
_NOT_SAID = re.compile(
    r"\b(?:pages?|sections?|sources?|it|they|this|that|which|none of (?:them|these))\s+"
    r"(?:do(?:es)?n['’]t|do(?:es)? not|did(?:n['’]t| not))\s+(?:\w+\s+)?"
    r"(?:say|state|specify|list|give|mention|name|include|describe|cover|address|explain|"
    r"confirm|show|provide|publish|tell)\b"
    r"|\bno (?:page|section|source)s? (?:says?|gives?|lists?|describes?|covers?|states?|names?)\b"
    r"|\b(?:isn['’]t|is not|aren['’]t|are not|wasn['’]t|was not) (?:stated|specified|listed|"
    r"published|given|covered|described|mentioned)\b",
    re.IGNORECASE)
MAX_SECOND_LOOKS = 2


def not_said(text: str) -> list[str]:
    """The reply's sentences that say the pages are silent on something, at most two."""
    found = [" ".join(piece.split()) for piece in statements(text)
             if piece.strip() and _NOT_SAID.search(piece)]
    return [re.sub(r"\s*\[[\d,\s–-]+\]", "", f) for f in found][:MAX_SECOND_LOOKS]


def gave_up(arguments: dict[str, Any], question: str) -> list[str]:
    """The parts a reply found no section for, for a second look with only their own search (at
    most two); else the reply's own sentences saying the pages do not say something; the whole
    question when the reply says it has no verified information but names no such part; none
    when it answered every part. A part no page can hold (the student's own records, something
    live, an opinion) is never looked for again."""
    parts = [p for p in arguments.get("parts") or [] if isinstance(p, dict)]
    if parts and all(p.get("not_on_pages") for p in parts):
        return []
    empty = [str(p.get("asked") or "").strip() for p in parts
             if not p.get("sections") and not p.get("not_on_pages")]
    empty = [a for a in empty if a][:MAX_SECOND_LOOKS]
    if empty:
        return empty
    text = str(arguments.get("text") or "")
    silent = not_said(text)
    if silent:
        return silent
    return [question] if _GAVE_UP.search(text) else []


def messages(question: str, earlier_user: str | None, offices: list[dict[str, Any]],
             today: date, programs: Sequence[dict[str, Any]] = (),
             calendar: dict[str, Any] | None = None,
             clubs: Sequence[dict[str, Any]] = (),
             people: Sequence[dict[str, Any]] = (),
             computed: Sequence[dict[str, Any]] = (),
             already_written: Sequence[str] = (),
             contacts_by_code: bool = False) -> list[dict[str, Any]]:
    given = {"today": f"{today:%A, %B} {today.day}, {today.year}", "question": question,
             **({"earlier_question": earlier_user} if earlier_user else {}),
             **({"contacts_written_by_code": True} if contacts_by_code else {}),
             **({"already_written": "\n\n".join(already_written)} if already_written else {}),
             **({"computed": list(computed)} if computed else {}),
             **({"programs": [{k: v for k, v in p.items() if not k.startswith("_")}
                              for p in programs]} if programs else {}),
             **({"calendar": calendar} if calendar else {}),
             **({"clubs": list(clubs)} if clubs else {}),
             **({"people": list(people)} if people else {}), "offices": offices}
    return [{"role": "system", "content": ONE_CALL_PROMPT},
            {"role": "user", "content": json.dumps(given, ensure_ascii=False)}]


# A reply with one or two statements the evidence does not hold keeps the rest: the statements
# are dropped, not the whole reply. Throwing the reply away sent the question to the four-call
# loop, which took 35-45 s and ran out of time on 11-02 ("deadline to withdraw from a class").
MAX_DROPPED = 2
MAX_DROPPED_SHARE = 0.35
_NOT_AN_END = re.compile(r"^(?:(?:[A-Za-z]\.){1,3}|(?:dr|mr|mrs|ms|prof|st|no|rm|vs|etc|e\.g|i\.e|"
                         r"ext|approx|dept|jr|sr|inc)\.)$", re.IGNORECASE)
_STATEMENT = re.compile(r"(?<=[.!?])(?:\s*\[[\d,\s–-]+\])*\s+(?=[A-Z(\"“])")


def statements(text: str) -> list[str]:
    """The reply's statements in order: each line, split after a full stop that ends a sentence
    (its citation marks stay with it). Joining them with their separators gives the text back."""
    pieces: list[str] = []
    for line in text.split("\n"):
        start = 0
        for m in _STATEMENT.finditer(line):
            before = line[start:m.start()].split()
            # "2. Submit", "Dr. Reck", "4:30 P.M. Monday" end no sentence.
            if not before or _NOT_AN_END.search(before[-1]) or (
                    len(before) == 1 and re.fullmatch(r"\d{1,2}[.)]", before[0])):
                continue
            pieces.append(line[start:m.start()] + line[m.start():m.end()].rstrip())
            pieces.append(" ")
            start = m.end()
        pieces.append(line[start:])
        pieces.append("\n")
    return pieces[:-1]


# Words that do not change what a statement says when only they differ ("movie and film events",
# "movie or film events"), and the citation marks, which say where, not what.
_SAME_SAYING = frozenset("a an the and or".split())
_MARKS = re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]")


def _said_words(statement: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", _MARKS.sub(" ", statement.casefold()))) - _SAME_SAYING


def without_repeats(text: str, already: Sequence[str]) -> tuple[str, list[str]]:
    """The model's text without the statements that say again what code already wrote before it,
    and those statements: a statement repeats one of code's when every word of it, citation marks
    and a few small words aside, is in that one statement (11-03 practice: a film question's
    answer came twice, code's list and then the model's copy of it). What code wrote is the
    answer to what it covers, so its own words are the ones that stay."""
    said = [_said_words(piece) for part in already for piece in statements(part) if piece.strip()]
    kept: list[str] = []
    dropped: list[str] = []
    for piece in statements(text):
        words = _said_words(piece) if piece.strip() else frozenset()
        if words and any(words <= other for other in said):
            dropped.append(piece.strip())
            if kept and kept[-1] == " ":
                kept.pop()
            continue
        kept.append(piece)
    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip(), dropped


def without_unsupported(text: str, check: Any) -> tuple[str, list[str]] | None:
    """The reply with each statement `check` finds a problem in removed, and the removed ones;
    None when more than MAX_DROPPED statements (or more than MAX_DROPPED_SHARE of the text)
    would go, when nothing cited is left, or when what is left still has a problem."""
    pieces = statements(text)
    kept: list[str] = []
    dropped: list[str] = []
    for piece in pieces:
        if piece.strip() and check(piece):
            dropped.append(piece.strip())
            if kept and kept[-1] == " ":
                kept.pop()
            continue
        kept.append(piece)
    left = re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()
    if (not dropped or len(dropped) > MAX_DROPPED
            or sum(map(len, dropped)) > MAX_DROPPED_SHARE * len(text)
            or not cited(left) or check(left)):
        return None
    return left, dropped


def reply(arguments: dict[str, Any], sources: list[dict[str, Any]],
          ) -> tuple[str, list[dict[str, Any]]]:
    """The reply with the sources it cited (its [n] marks, grouped ones too), and those as
    citations."""
    text = re.sub(r"\s*\[(?!\d)[A-Za-z][\w ,.&'-]*\](?!\()", "",
                  str(arguments.get("text") or "")).strip()
    used = sorted({n for n in cited(text) if 1 <= n <= len(sources)})
    linked = [sources[n - 1] for n in used if sources[n - 1].get("url")]
    if linked:
        text += "\n\nSources: " + " ".join(f"[{s['n']}]({s['url']})" for s in linked)
    return text, [{"id": f"section-{s['n']}", "title": str(s.get("title") or ""),
                   "url": s["url"]} for s in linked]


_MONTH_NAMES = ("january february march april may june july august september october november "
                "december").split()
# A dated change a page states ("Effective July 1, 2026 – Dean Susan Hangen", "as of 6/12/25"):
# code says whether its date has passed, so a page listing both the old and the new is read as
# of today rather than as two pages that differ.
_DATED = re.compile(
    r"\b(effective|beginning|starting|as of)\s+(?:on\s+)?"
    r"(?:(" + "|".join(_MONTH_NAMES) + r")\s+(\d{1,2}),?\s+(\d{4})|(\d{1,2})/(\d{1,2})/(\d{2,4}))"
    r"([^.;]{0,100})", re.IGNORECASE)
MAX_DATED = 4
# "Beginning" or "starting" after a session or term starts that session, not a change: "7 week
# class session beginning August 26, 2026 100 % Refund: August 28" is a refund table's row, which
# was read as "has passed, so this is in effect now" (11-03, a drop-and-refund question).
_STARTS_A_TERM = re.compile(
    r"\b(?:sessions?|class(?:es)?|courses?|semesters?|terms?|weeks?|modules?|blocks?|parts?|"
    r"programs?|year)\s*$", re.IGNORECASE)
# A second date in the words after it: a schedule of dates, not one change.
_ANOTHER_DATE = re.compile(
    r"\b(?:" + "|".join(_MONTH_NAMES) + r")\s+\d{1,2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b",
    re.IGNORECASE)


def dated_statements(offices: Sequence[dict[str, Any]], today: date, question: str,
                     names: Sequence[str] = ()) -> list[dict[str, Any]]:
    """For each section given that states a change from a date about what was asked (a word of
    the question of five letters or more, or a person given by name), whether that date has
    passed: at most MAX_DATED, as computed facts with the section's number."""
    asked = {w for w in re.findall(r"[a-z]{5,}", question.casefold())} | {
        w for name in names for w in re.findall(r"[a-z]{3,}", name.casefold())}
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for office in offices:
        for section in office.get("sections") or []:
            text = " ".join(str(section.get(k) or "") for k in ("text", "section_words"))
            for found in _DATED.finditer(text):
                try:
                    if found.group(2):
                        when = date(int(found.group(4)), _MONTH_NAMES.index(
                            found.group(2).casefold()) + 1, int(found.group(3)))
                    else:
                        year = int(found.group(7))
                        when = date(year + 2000 if year < 100 else year, int(found.group(5)),
                                    int(found.group(6)))
                except ValueError:
                    continue
                if found.group(1).casefold() in ("beginning", "starting") and (
                        _STARTS_A_TERM.search(text[:found.start()])
                        or _ANOTHER_DATE.search(found.group(8))):
                    continue
                said = found.group(0)
                if found.group(1).casefold() == "as of":  # "Not active as of 6/12/25."
                    said = re.split(r"[.;]", text[:found.start()])[-1][-100:] + found.group(0)
                said = " ".join(said.split()).strip(" ,–-")
                if said.casefold() in seen or not asked & set(re.findall(r"[a-z]+",
                                                                         said.casefold())):
                    continue
                seen.add(said.casefold())
                facts.append({"fact": f'A page says "{said}" [{section["n"]}]: '
                                      f"{when:%B} {when.day}, {when.year} "
                                      + ("has passed, so this is in effect now."
                                         if when <= today else
                                         "is still to come, so this is not in effect yet."),
                              "n": section["n"]})
                if len(facts) >= MAX_DATED:
                    return facts
    return facts


# The every-word check (Dan 11-02, "no made-up facts"): each statement of a reply may use only words
# the facts it cites hold. Report only for now: it lists what it would cut, and cuts nothing.
_GRAMMAR = frozenset("""
the and for with from that this these those you your yours are was were been being can could will
would must may might not have has had into onto over under than then there their they them its
also only just more most less such each every any all both either neither but nor yet some same
about after before during while when where which who whom whose what why how here very much many
other another one ones per via within without upon among between because since until unless
though although whether does did done doing make makes made get gets got use used using say says
said see listed lists list given gives give note notes noted states stated state shows show
page pages section sections site sites source sources verified information official published
publish publishes college ramapo office offices contact contacts email phone call visit student
students you're it's don't doesn't isn't aren't can't won't i'm we our us please need needs
able like way ways first next last new also yes include includes including part parts asked ask
handle handles handled handling offer offers offered provide provides provided describe describes
described mean means meaning allow allows tell tells told try tries help helps helping her his
him she hers own well officially according different differ differs difference either whether
regarding related another both
""".split())


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "ed", "es", "s", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _content(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]+", text.casefold().replace("’", "'"))
            if len(w) >= 3 and w not in _GRAMMAR}


def unbacked(text: str, evidence: dict[int, str], shared: str) -> list[dict[str, Any]]:
    """Each statement's words that neither the facts its marks cite nor the shared words (names,
    the question, fixed wording) hold: what the every-word check would cut."""
    held_shared = _content(shared)
    found = []
    # A mark covers its paragraph or list: the model cites a list's steps or a paragraph's
    # sentences once, at its end (88 of 124 statements flagged on 11-02 had no mark of their own).
    for paragraph in text.split("\n\n"):
        block = cited(paragraph)
        for piece in statements(paragraph):
            statement = piece.strip()
            if not statement or statement.startswith("Sources:"):
                continue
            marks = cited(statement) or block
            held = held_shared.union(*(_content(evidence.get(n, "")) for n in marks))
            words = sorted(_content(re.sub(r"\[[\d,\s–-]+\]", "", statement)) - held)
            if words:
                found.append({"statement": statement[:240], "words": words[:12],
                              **({} if marks else {"uncited": True})})
    return found


def evidence_by_mark(offices: Sequence[dict[str, Any]], computed: Sequence[dict[str, Any]],
                     *entries: Sequence[dict[str, Any]]) -> dict[int, str]:
    """Everything each source number stands for, as text: a section with its office's name and
    contacts, a computed fact, a person's, program's, event's or club's entry."""
    by_n: dict[int, list[str]] = {}
    for office in offices:
        contacts = json.dumps({k: office.get(k) for k in ("office", "email", "phones", "rooms")},
                              ensure_ascii=False)
        for section in office.get("sections") or []:
            by_n.setdefault(section["n"], []).append(
                json.dumps(section, ensure_ascii=False) + contacts)
    for fact in computed:
        if isinstance(fact, dict) and isinstance(fact.get("n"), int):
            by_n.setdefault(fact["n"], []).append(str(fact.get("fact", "")))
    for listed in entries:
        for entry in listed or []:
            if isinstance(entry, dict) and isinstance(entry.get("n"), int):
                by_n.setdefault(entry["n"], []).append(json.dumps(entry, ensure_ascii=False))
    return {n: " ".join(texts) for n, texts in by_n.items()}
