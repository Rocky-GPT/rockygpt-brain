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
from rockygpt_brain.events import asks_clubs, asks_events
from rockygpt_brain.people import asks_people
from rockygpt_brain.retrieval.meaning import MeaningIndex, MeaningScores

PER_PART = 10  # Each part's own best units: a "how do I apply" part keeps its application section.
PER_PAGE = 2  # At most this many from one page, so its near-copies don't crowd out the next page.
MAX_UNITS = 24
MAX_PARTS = 3
SECTION_WORDS_CHARS = 2000

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
- When sections disagree about the same date, amount or rule for the same people, place and term,
  give each with its section number and say the pages differ; never pick one silently. Different
  values for different groups, places or terms are not a disagreement: say which applies to whom.
- Name the office before its steps and contacts. Give contacts only of offices whose sections you
  used, or the right office when no section answers.
- Answer every part of the question. Before saying a part is not covered, check every section
  given, including its section_words. For a part no section answers, say once that you don't have
  verified information about it, and point to the office most likely to help with its contacts.
- computed are facts code worked out from the catalog (whether a program is published at the
  level asked, credits and their sums, Gen Ed attributes, catalog credit totals): state every one
  that answers the question, exactly as given, with its number. They override any page section:
  a page that disagrees with the catalog is out of date, and is not repeated.
- A program's requirements (programs.requirements) were written out by code from the catalog: say
  them as given, every course and how many of a list to take, with their notes; never add up
  credits or courses yourself. Name the program and its degree; when also_published lists its
  other levels, name them in one short line. A course (programs entries with "course") is said
  with its credits, prerequisites and attributes as given. A graduation_plan is the catalog's
  recommended sequence for the admission year it names: say which year it is for, and give its
  semesters and totals as written. catalog_programs lists every published program by level: a
  major, minor or program it does not list is not offered in the catalog; say so plainly.
- calendar.events are the college calendar's events in the window code chose (calendar.window):
  give each one's title, day and time, place and host as written, with its number. When
  calendar.topic_matched is false, first say the calendar lists nothing for what was asked in
  that window, then give what the sections say about it (a fair's season, a series' next
  date), then offer the window's other events; with no events, say so and give
  calendar_url. When topic_matched is true the events are those about the topic, among
  calendar.events_on_the_calendar_in_it: never call one the only event in the window. tickets
  FREE means free admission. clubs are directory entries:
  give each club's name, category and page as written. Never add events, times or clubs.
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
- Write a phone number as (XXX) XXX-XXXX with its own digits.
- Cite each statement with its section number like [3]. Short plain sentences; numbered steps are
  fine. Under 1,600 characters. No greeting.
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


def routes_here(message: str) -> bool:
    """Whether a message is a how-to or policy question for the one-call path."""
    text = message.casefold()
    if _SERVICES.search(text) and not _MEAL_PLAN.search(text) and not asks_academics(text) \
            and not asks_events(text) and not asks_clubs(text) and not asks_people(text):
        return False
    howto = _HOWTO.search(text)
    if (_HOURS.search(text) or _CONTACT.search(text)) and not howto:
        return False
    return True


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
                **{k: value[k] for k in ("applies_to", "handled_by", "term", "past_term")
                   if value.get(k)}})
        offices.append({"office": (found.get("entity") or {}).get("name") or entity_id,
                        "email": values("email"), "phones": values("phones"),
                        "rooms": values("offices"), "sections": sections})
    return offices, sources


SECOND_LOOK_UNITS = 12
_GAVE_UP = re.compile(r"don.t have verified")


def gave_up(arguments: dict[str, Any], question: str) -> list[str]:
    """The parts a reply found no section for, for a second look with only their own search (at
    most two); the whole question when the reply says it has no verified information but names
    no such part; none when it answered every part. A part no page can hold (the student's own
    records, something live, an opinion) is never looked for again."""
    parts = [p for p in arguments.get("parts") or [] if isinstance(p, dict)]
    if parts and all(p.get("not_on_pages") for p in parts):
        return []
    empty = [str(p.get("asked") or "").strip() for p in parts
             if not p.get("sections") and not p.get("not_on_pages")]
    empty = [a for a in empty if a][:2]
    if empty:
        return empty
    return [question] if _GAVE_UP.search(str(arguments.get("text") or "")) else []


def messages(question: str, earlier_user: str | None, offices: list[dict[str, Any]],
             today: date, programs: Sequence[dict[str, Any]] = (),
             calendar: dict[str, Any] | None = None,
             clubs: Sequence[dict[str, Any]] = (),
             people: Sequence[dict[str, Any]] = (),
             computed: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    given = {"today": f"{today:%A, %B} {today.day}, {today.year}", "question": question,
             **({"earlier_question": earlier_user} if earlier_user else {}),
             **({"computed": list(computed)} if computed else {}),
             **({"programs": list(programs)} if programs else {}),
             **({"calendar": calendar} if calendar else {}),
             **({"clubs": list(clubs)} if clubs else {}),
             **({"people": list(people)} if people else {}), "offices": offices}
    return [{"role": "system", "content": ONE_CALL_PROMPT},
            {"role": "user", "content": json.dumps(given, ensure_ascii=False)}]


def reply(arguments: dict[str, Any], sources: list[dict[str, Any]],
          ) -> tuple[str, list[dict[str, Any]]]:
    """The reply with the sources it cited (its [n] marks), and those as citations."""
    text = str(arguments.get("text") or "").strip()
    used = sorted({int(n) for n in re.findall(r"\[(\d+)\]", text) if 1 <= int(n) <= len(sources)})
    cited = [sources[n - 1] for n in used if sources[n - 1].get("url")]
    if cited:
        text += "\n\nSources: " + " ".join(f"[{s['n']}]({s['url']})" for s in cited)
    return text, [{"id": f"section-{s['n']}", "title": str(s.get("title") or ""),
                   "url": s["url"]} for s in cited]
