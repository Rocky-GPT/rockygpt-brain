"""Faculty and staff: which people a question names, read through the shared reader.

A person is named by their whole directory name ("Rikki Abzug"), or by a last name that is exactly
one person's ("professor abzug"); a question about who researches or teaches something finds people
by their card's meaning (name, title, school, interests). Naming a person establishes none of their
facts: the directory and their faculty profile do.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Any

from rockygpt_brain.plain_writer import literal
from rockygpt_brain.retrieval.entity_facts import discovery_tokens

MAX_PEOPLE = 4
# "Office hours" alone is not one: offices have office hours too ("the Student Accounts office
# hours" went to the faculty path on 11-02); a person's are asked with a title or their name.
_PEOPLE_WORDS = re.compile(
    r"\b(?:professors?|prof|profs|dr|doctor|teachers?|instructors?|faculty|lecturers?|"
    r"teach(?:es|ing)?|taught|chair|advisors?|advisers?|dean|researcher|"
    r"research(?:es)?|in charge|who runs|head of"
    r"|staff)\b")
_TITLES = discovery_tokens("professor prof dr doctor mr mrs ms miss dean")
# A card found by meaning, only for a question about a topic ("who teaches business ethics"): on
# 10 sample questions (10-22) topic questions scored 0.42-0.60 against their best person and
# unrelated ones at most 0.23; a question naming a person no one in the directory is ("professor
# smith email", 0.45 against strangers) is never answered by meaning.
PERSON_FLOOR, PERSON_NEAR = 0.40, 0.05
# Words a question about a person uses that are not anyone's name, never read as a misspelt
# last name ("room" is not Root).
_QUESTION_WORDS = discovery_tokens(
    "room rooms office offices phone email number hours hour class classes course courses "
    "teach teaches teacher research major minor department school chair dean advisor adviser "
    "where when what which does have need about from with there their this that thesis senior "
    "junior freshman student students help contact reach find name title building")
_CONTACT_ASK = re.compile(r"\b(?:e-?mail|phone|number|office|contact|reach|extension)\b")
_TOPIC_ASK = re.compile(r"\b(?:who|teach\w*|research\w*|stud(?:y|ies)|experts?|chair|"
                        r"specializ\w*|works? on)\b")


def fold(text: str) -> str:
    """Name text as a student types it: accents and marks dropped ("Renée" is renee, "Katicˊ"
    is katic) and an apostrophe inside a name joined ("D’Antonio" is dantonio); a possessive
    's is dropped first."""
    text = re.sub(r"(?<=\w)['’ʼ]s\b", "", text)
    text = "".join(c for c in unicodedata.normalize("NFKD", text)
                   if unicodedata.category(c) not in ("Mn", "Lm", "Sk"))
    return re.sub(r"(?<=\w)['’`´](?=\w)", "", text)


def name_tokens(text: str) -> frozenset[str]:
    return discovery_tokens(fold(text))


def asks_people(message: str) -> bool:
    return bool(_PEOPLE_WORDS.search(message.casefold()))


def asks_people_topic(message: str) -> bool:
    """Whether a question asks who works on a topic, rather than about a person by name."""
    return asks_people(message) and bool(_TOPIC_ASK.search(message.casefold()))


# A title before one letter that stands in for a name ("Professor A or Professor B", "prof x"):
# the student named no one, so no one in the directory is that person. 11-03 repeat test: "who's
# better for cs, professor A or professor B?" was answered every time with two real computer
# science professors found by meaning, as if they were A and B. An initial before a name ("Dr. J.
# Smith") is not one, and neither are "a" and "I" ("a professor a student can see").
_STAND_IN = re.compile(r"\b(?i:professor|prof|dr|doctor|mr|mrs|ms|miss|dean)\.?\s+"
                       r"(?:[A-HJ-Z]|[b-hj-z])\b(?!\.?\s*[A-Z][a-z])")


def stand_ins(message: str) -> list[str]:
    """The people a message names only by a letter standing in for a name, as written."""
    return list(dict.fromkeys(" ".join(m.group(0).split()) for m in _STAND_IN.finditer(message)))


def stand_in_fact(said: Sequence[str]) -> str:
    """What code tells the writer of stand-in names: they name no one, and no one is them."""
    one = len(said) == 1
    names = said[0] if one else f"{', '.join(said[:-1])} and {said[-1]}"
    return (f"The question names no one: {names} {'is a letter' if one else 'are letters'} "
            f"standing in for {'a name' if one else 'names'}, so no person in the directory "
            f"may be said to be {'that person' if one else 'any of them'}.")


# Degrees after a directory name ("Joyce Shim, Ph.D., MIA, MSW") are not part of what a
# student types.
_CREDENTIALS = re.compile(r"(?:,\s*[A-Z][A-Za-z.]{0,7})+\s*$")


def _name(person: dict[str, Any]) -> str:
    return _CREDENTIALS.sub("", str(person["name"])).strip()


def find_people(people: Sequence[dict[str, Any]], message: str,
                ordinary: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """The people a message names: every word of a directory name, and a last name that is
    exactly one person's ("Christina Chung or Alex Biagioli" names both). A hyphenated last name
    names its person whole ("williams-goldstein") even where each half is someone else's too. A
    last name several people share names all of them (the reply asks which one); a two-letter
    last name ("xu") counts only after a title word. A word in `ordinary` (the published pages'
    own words: "letter", "mentor") is never read as a misspelt name. At most MAX_PEOPLE."""
    words = name_tokens(message) - _TITLES
    shortest = 2 if name_tokens(message) & _TITLES else 3
    whole = [p for p in people if (name := frozenset(w for w in name_tokens(_name(p))
                                                     if len(w) > 1))
             and len(name) >= 2 and name <= words]
    if whole:
        covered = frozenset().union(*(name_tokens(_name(p)) for p in whole))
        rest = [p for p in _last_names(people, message, words - covered, shortest, ordinary)
                if p not in whole and not p.get("near_spelling")]
        return [*whole, *rest][:MAX_PEOPLE]
    return _last_names(people, message, words, shortest, ordinary)


def _last_names(people: Sequence[dict[str, Any]], message: str, words: frozenset[str],
                shortest: int, ordinary: frozenset[str]) -> list[dict[str, Any]]:
    """The people a message names by last name alone, else by a near spelling of one."""
    # A last name that is an ordinary word of the pages is a person only in a question about one
    # ("is the food good" names no Professor Norflus-Good); "Terrizzi" is a name anywhere.
    cued = bool(asks_people(message) or _CONTACT_ASK.search(message.casefold()))
    if not cued:
        words = frozenset(w for w in words if ordinary and w not in ordinary)
    # A possessive typed without its apostrophe ("professor brissons email") names Brisson.
    words = words | {w[:-1] for w in words if len(w) > 4 and w.endswith("s")}
    owners: dict[frozenset[str], list[dict[str, Any]]] = {}
    for person in people:
        parts = fold(_name(person)).split()
        last = discovery_tokens(parts[-1]) if parts else frozenset()
        keys = {frozenset(last)} | {frozenset({token}) for token in last}
        for key in keys:
            if key and min(len(token) for token in key) >= shortest:
                owners.setdefault(key, []).append(person)
    named = sorted(((key, matches) for key, matches in owners.items() if key <= words),
                   key=lambda pair: -len(pair[0]))
    found: list[dict[str, Any]] = []
    for _, matches in named:
        if len(matches) == 1 and matches[0] not in found:
            found.append(matches[0])
    if not found:
        for _, matches in named:
            if len(matches) <= MAX_PEOPLE:
                return matches
    if not found and not named and cued:
        return _near_names(people, words - ordinary)
    return found[:MAX_PEOPLE]


def _distance(a: str, b: str) -> int:
    """Edits between two words, a swap of neighbouring letters counting as one."""
    rows = [list(range(len(b) + 1))]
    for i, x in enumerate(a, 1):
        row = [i]
        for j, y in enumerate(b, 1):
            row.append(min(rows[-1][j] + 1, row[j - 1] + 1, rows[-1][j - 1] + (x != y)))
            if i > 1 and j > 1 and x == b[j - 2] and a[i - 2] == y:
                row[j] = min(row[j], rows[-2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def _near_names(people: Sequence[dict[str, Any]],
                words: frozenset[str] | set[str]) -> list[dict[str, Any]]:
    """The people whose last name is a near spelling of a word the question uses ("pefer" for
    Peffer, "howenstien" for Howenstein): one edit for names of four to six letters, two from
    seven. Each is marked as a near spelling, so the reply asks whether that is who was meant
    rather than answering as if it were."""
    words = words - _QUESTION_WORDS
    found: list[dict[str, Any]] = []
    for person in people:
        parts = fold(_name(person)).split()
        last = sorted(discovery_tokens(parts[-1]), key=len)[-1:] if parts else []
        for token in last:
            allowed = 1 if len(token) <= 6 else 2
            if len(token) >= 4 and any(
                    abs(len(w) - len(token)) <= allowed and len(w) >= 4
                    and w[0] == token[0] and (_distance(w, token) <= allowed
                                              or sorted(w) == sorted(token))
                    for w in words):
                found.append({**person, "near_spelling": True})
    # A first name the question also uses picks among near spellings ("mark howenstien").
    first_named = [p for p in found if _name(p).split()
                   and name_tokens(_name(p).split()[0]) & words]
    return (first_named or found)[:MAX_PEOPLE]


# A question asking for everyone in a field ("list the nursing faculty with their emails").
_LIST_ASK = re.compile(r"\b(?:list|all|everyone|every|who are the|whos? are the)\b")
MAX_LISTED = 25
_LIST_FILLER = discovery_tokens(
    "list all everyone every who are the faculty staff professors professor members member "
    "their with emails email phone numbers contact contacts give can get please department "
    "dept school program programs at ramapo")


def asks_people_list(message: str) -> bool:
    return asks_people(message) and bool(_LIST_ASK.search(message.casefold()))


def listed(directory: Sequence[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """Everyone whose title (else department or school) has every remaining word of a list
    question ("nursing" in "list the nursing faculty"), each as name, title, email and profile
    page only (the caller gives at most MAX_LISTED and says how many there are)."""
    def stems(text: str) -> set[str]:
        return {w[:-1] if len(w) > 4 and w.endswith("s") else w
                for w in discovery_tokens(text) if len(w) > 2}
    words = stems(message) - stems(" ".join(_LIST_FILLER | _QUESTION_WORDS))
    if not words:
        return []

    def has(entry: dict[str, Any], keys: tuple[str, ...]) -> bool:
        return words <= stems(" ".join(str(entry.get(k) or "") for k in keys))
    # A field in a title ("Professor of Nursing") before a department or school name, which
    # also holds fields its other faculty do not teach ("Science, Nursing, and Health"); a
    # question naming a school ("the business school faculty") asks for its school.
    by_title = [e for e in directory if has(e, ("title",))]
    by_school = [e for e in directory if has(e, ("title", "department", "school"))]
    found = (by_school if re.search(r"\bschool\b", message.casefold()) or not by_title
             else by_title)
    return [{k: entry.get(k) for k in ("id", "name", "title", "email", "profile_url")
             if entry.get(k)} for entry in found]


def by_meaning(scores: Sequence[tuple[str, float]], names: dict[str, str]) -> list[dict[str, Any]]:
    """The people whose cards best match the question: above the floor, near the best."""
    if not scores or scores[0][1] < PERSON_FLOOR:
        return []
    return [{"id": entity_id, "name": names[entity_id]} for entity_id, score in scores
            if score >= max(PERSON_FLOOR, scores[0][1] - PERSON_NEAR) and entity_id in names
            ][:MAX_PEOPLE]


def page_of(entry: dict[str, Any]) -> str | None:
    """The page a person's facts are cited to: their faculty profile, else the office page that
    lists them as staff (fix 4, 11-04)."""
    return entry.get("profile_url") or entry.get("staff_page")


def person_evidence(facts: dict[str, Any], n: int, near_spelling: bool = False) -> dict[str, Any]:
    """What the reply is given for one person: the directory's and profile's published facts,
    and whether the question named them only by a near spelling."""
    prop = facts["properties"][0]
    value = prop["values"][0]["value"] if prop["values"] else {"name": facts["entity"]["name"]}
    return {"n": n, **({"near_spelling_of_asked_name": True} if near_spelling else {}),
            **{k: v for k, v in value.items() if v not in (None, [], "")}}


# What code works out for a people question, for the reply to state as it is (the way the
# shuttle's next departure is): each person's title and the fields asked, or that the directory
# and profile publish none; the people a shared name could mean, narrowed by the school or field
# the question names; and who leads a school or field by their directory titles.

# Title words that say rank or role, not a field ("Associate Professor of Chemistry" is
# chemistry), and common words a question uses for other things. "In Residence" is a role
# ("Entrepreneur in Residence"), not a field: fix check 11-04, "who is in charge of residence
# life" was answered with the business school's dean.
_NOT_FIELD = discovery_tokens(
    "professor associate assistant adjunct visiting distinguished emeritus emerita retired "
    "residence interim lecturer instructor librarian senior teaching specialist fellow executive "
    "director dean chair convener coordinator coord supervisor secretary administrator "
    "the and of in for at he him she her they them it s d te glo master post program "
    "programs studies school college lab unit desk project projects series services system "
    "systems resources development course learning research reading writing work social "
    "science sciences data information technology world public policy applied global "
    "association institute organization industry women unit circulation reference "
    "instruction technical electronic web budget government documents assessment loan "
    "scholarship monograph reserves")


def schools_of(directory: Sequence[dict[str, Any]],
               offices: Sequence[dict[str, Any]]) -> dict[str, frozenset[str]]:
    """Each school in the directory with the words that name it: its own name's distinctive
    words and its school office's other names ("SNH" for the School of Science, Nursing, and
    Health); "(Retired)" and "(Adjunct)" listings belong to their school."""
    names = {_school(e.get("school")) for e in directory} - {""}
    aliases: dict[str, set[str]] = {}
    for office in offices:
        name = re.sub(r"\s+office$", "", str(office.get("name") or ""), flags=re.I).strip()
        for alias in office.get("aliases") or []:
            aliases.setdefault(name.casefold(), set()).update(
                name_tokens(re.sub(r"\s+office$", "", str(alias), flags=re.I)))
    return {school: frozenset((name_tokens(school) - _SCHOOL_GENERIC)
                              | aliases.get(school.casefold(), set()))
            for school in names}


_SCHOOL_GENERIC = discovery_tokens("school of and faculty staff work")


def _school(value: Any) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", str(value or "")).strip()


def _phrase(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", fold(text).casefold()))


def schools_by_name(message: str, identities: Sequence[dict[str, Any]],
                    schools: Sequence[str]) -> list[tuple[str, list[str]]]:
    """The schools a question names by a whole name of two or more words that their identities
    have, other names included, with the name used: "School of Social Science and Human
    Services" was one school and is now another name of two (its site moved to SSSW; AHE took its
    education programs), which word counts cannot tell from the School of Science."""
    asked = f" {_phrase(message)} "
    current = {s.casefold(): s for s in schools}
    found: dict[str, list[str]] = {}
    for identity in identities:
        school = current.get(str(identity.get("name") or "").casefold())
        if school is None:
            continue
        for name in [identity["name"], *(identity.get("aliases") or [])]:
            words = _phrase(str(name))
            if len(words.split()) >= 2 and f" {words} " in asked:
                found.setdefault(str(name), []).append(school)
    # A name inside a longer one named ("School of Business" in "Anisfield School of Business").
    return [(name, of) for name, of in found.items()
            if not any(f" {_phrase(name)} " in f" {_phrase(other)} " and other != name
                       for other in found)]


def school_asked(message: str, schools: dict[str, frozenset[str]]) -> str | None:
    """The school a question names by its words ("the business school", "SNH"), the one with
    the most of them; None when it names none."""
    words = name_tokens(message)
    scored = sorted(((len(tokens & words), school) for school, tokens in schools.items()),
                    reverse=True)
    if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return scored[0][1]


def fields_asked(message: str, directory: Sequence[dict[str, Any]],
                 exclude: frozenset[str] = frozenset()) -> set[str]:
    """The fields a question names that some directory title names ("chemistry", "nursing"),
    other than the words in `exclude` (the names asked)."""
    vocabulary = {w for e in directory for w in name_tokens(str(e.get("title") or ""))
                  if len(w) > 3} - _NOT_FIELD
    return (set(name_tokens(message)) & vocabulary) - exclude


def narrow(items: Sequence[dict[str, Any]], message: str,
           schools: dict[str, frozenset[str]]) -> list[dict[str, Any]]:
    """Among people who share the last name asked, those whose title or school the question's
    other words name ("prof lee from the business school"); all of them when none or every one
    is named that way."""
    lasts = {fold(_name(i)).split()[-1].casefold() for i in items if _name(i).split()}
    if len(items) < 2 or len(lasts) != 1:
        return list(items)
    words = set(name_tokens(message)) - _TITLES - _QUESTION_WORDS

    def named(item: dict[str, Any]) -> int:
        about = set(name_tokens(f"{item.get('title') or ''} {item.get('school') or ''}"))
        about |= schools.get(_school(item.get("school")), frozenset())
        return len((about - name_tokens(_name(item)) - _SCHOOL_GENERIC) & words)
    best = max(named(i) for i in items)
    kept = [i for i in items if named(i) == best]
    return kept if best and len(kept) < len(items) else list(items)


# The fields a question asks for, by its words; a question asking none of them gets the
# contact fields.
_ASKED_FIELDS = (
    ("email", "email address", re.compile(r"\be-?mails?\b|\bcontact\b|\breach\b")),
    ("phone", "phone number", re.compile(r"\bphone\b|\bnumber\b|\bcall\b|\bextension\b|"
                                         r"\bcontact\b|\breach\b")),
    ("office", "office room", re.compile(r"\boffice\b(?!\s*hours)|\broom\b|\bwhere\b|"
                                         r"\blocated\b|\blocation\b")),
    ("office_hours", "office hours", re.compile(r"\bhours\b|\bavailab\w*|\bmeet\b|"
                                                r"\bdrop in\b|\bfree\b")),
    ("courses", "courses", re.compile(r"\bteach\w*|\bcourses?\b|\bclass(?:es)?\b")),
    ("research_interests", "research interests",
     re.compile(r"\bresearch\w*|\binterests?\b|\bwork(?:s)? on\b|\bexpert\w*|\bspeciali\w*|"
                r"\bstud(?:y|ies)\b")),
)
_CONTACT_FIELDS = ("email", "phone", "office")


def _asked(message: str) -> list[tuple[str, str]]:
    asked = [(key, label) for key, label, _ in _ASKED_FIELDS if key in fields_named(message)]
    return asked or [(key, label) for key, label, _ in _ASKED_FIELDS if key in _CONTACT_FIELDS]


def fields_named(message: str) -> list[str]:
    """The person fields a question names by its words ("what is their email and office
    number" names email, phone and office), none by default."""
    text = fold(message).casefold()
    return [key for key, _, words in _ASKED_FIELDS if words.search(text)]


def _value(value: Any) -> str:
    return "; ".join(map(str, value)) if isinstance(value, list) else str(value)


def computed_answers(items: Sequence[dict[str, Any]], message: str,
                     directory: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """For the people a question names (already read, numbered): when several share the name
    asked, each with title and school and that the student should say which; else each one's
    directory title and school, every field asked as published or that neither the directory nor
    the profile publishes it, and any field the question names that their title does not."""
    named = [i for i in items if i.get("name")]
    if not named:
        return []
    lasts = {fold(_name(i)).split()[-1].casefold() for i in named if _name(i).split()}
    if len(named) > 1 and len(lasts) == 1:
        listing = "; ".join(f"{_name(i)} ({i.get('title') or 'no title listed'}, "
                            f"{i.get('school') or 'no school listed'}) [{i['n']}]" for i in named)
        return [{"fact": f"{len(named)} people in the directory share the name asked: {listing}. "
                         "Ask which one the student means.", "n": named[0]["n"]}]
    facts: list[dict[str, Any]] = []
    asked = _asked(message)
    for item in named:
        name, n = _name(item), item["n"]
        if item.get("near_spelling_of_asked_name"):
            facts.append({"fact": f"No one in the directory has exactly the name asked; the "
                                  f"nearest is {name}. Ask whether the student means {name}.",
                          "n": n})
        title, school = item.get("title"), _school(item.get("school"))
        facts.append({"fact": f"{name}'s directory title is {title}" if title else
                              f"The directory lists no title for {name}", "n": n})
        if school:
            facts[-1]["fact"] += f", in the {school}."
        else:
            facts[-1]["fact"] += "."
        others = fields_asked(message, directory, name_tokens(name))
        missing = {w for w in others if w not in name_tokens(f"{title or ''} {school}")}
        if title and missing:
            facts.append({"fact": f"{name}'s title does not name {', '.join(sorted(missing))}.",
                          "n": n})
        for key, label in asked:
            if item.get(key):
                where = ("on the faculty profile, as written" if key in
                         ("office_hours", "courses", "research_interests") else "per the directory")
                facts.append({"fact": f"{name}'s {label}, {where}: {_value(item[key])}.", "n": n})
            elif key == "office_hours":
                facts.append({"fact": f"{name}'s faculty profile lists no office hours.", "n": n})
            elif key in _PROFILE_FIELDS:
                facts.append({"fact": f"{name}'s faculty profile lists no {label}.", "n": n})
            else:
                facts.append({"fact": f"Neither the directory nor {name}'s faculty profile "
                                      f"publishes {'an' if label[0] in 'aeiou' else 'a'} "
                                      f"{label}.", "n": n})
    return facts


# A question saying nothing but who one named person is ("Who is Professor Haase?") is answered
# by code, as are the fields a question names (person_text).
_WHO_FILLER = name_tokens("who whos is was are professor prof dr doctor mr mrs ms miss the at "
                          "ramapo college here")
_FIELD_LABELS = {"email": "Email", "phone": "Phone", "office": "Office",
                 "office_hours": "Office hours", "courses": "Courses on the faculty profile",
                 "research_interests": "Research interests on the faculty profile"}
_PROFILE_FIELDS = ("office_hours", "courses", "research_interests")


def only_who(message: str, person: dict[str, Any]) -> bool:
    """Whether a question asks only who one named person is: its words are their name, a title
    said before it ("Dean Hangen") and filler."""
    return not name_tokens(about_named(message, [person])) - _WHO_FILLER


def contact_text(item: dict[str, Any]) -> str | None:
    """A person's directory contacts in code's words ("Email: ... Phone: ... Office: ... [n]"),
    or None when the directory publishes none."""
    said = [f"{_FIELD_LABELS[key]}: {item[key] if key != 'office' else literal(item[key])}."
            for key in _CONTACT_FIELDS if item.get(key)]
    return " ".join(said) + f" [{item['n']}]" if said else None


def person_text(item: dict[str, Any], message: str) -> str | None:
    """Code's answer about one person a question names: who they are by the directory (title and
    school), then each field asked as published, or that it is not published [their number].
    None for a name only near one in the directory, which the student is asked to confirm."""
    if item.get("near_spelling_of_asked_name") or not item.get("name"):
        return None
    title, school = item.get("title"), _school(item.get("school"))
    # A staff member is said with the office whose page lists them (fix 4, 11-04).
    office = (item.get("department") if item.get("staff_page") and not school
              and str(item.get("department") or "").casefold() not in str(title).casefold()
              else None)
    said = [(f"{literal(_name(item))} is {literal(title)}" if title
             else f"The directory lists {literal(_name(item))} with no title")
            + (f" in the {literal(school)}." if school
               else f" ({literal(office)})." if office else ".")]
    for key, label in _asked(message):
        value = item.get(key)
        if value:
            shown = "; ".join(re.sub(r"^\s*office hours\s*:\s*", "", str(v), flags=re.I)
                              for v in (value if isinstance(value, list) else [value]))
            shown = shown.strip().rstrip(".")
            said.append(f"{_FIELD_LABELS[key]}: "
                        f"{shown if key in ('email', 'phone') else literal(shown)}.")
        elif item.get("staff_page") and not item.get("profile_url"):
            said.append(f"The office's staff page lists no {label}.")
        elif key in _PROFILE_FIELDS:
            said.append(f"The faculty profile lists no {label}.")
        else:
            said.append(f"Neither the directory nor the faculty profile lists "
                        f"{'an' if label[0] in 'aeiou' else 'a'} {label}.")
    return " ".join(said) + f" [{item['n']}]"


# A question about who leads a school or a field: its dean, or a department's chair or head.
_LEAD_ASK = re.compile(r"\b(?:deans?|chair(?:s|person|man|woman)?|conveners?|"
                       r"(?:department|dept|program) heads?|head of|in charge of)\b")
_LEAD_TITLE = re.compile(r"\b(?:dean|chair\w*|convener|director|coordinator|head)\b", re.I)
_DEAN_TITLE = re.compile(r"\b(?:(assistant|associate|vice)\s+)?(interim\s+)?dean\b", re.I)


def asks_leadership(message: str) -> bool:
    text = fold(message).casefold()
    return bool(_LEAD_ASK.search(text)) and "dean of students" not in text


def about_named(message: str, people: Sequence[dict[str, Any]]) -> str:
    """A message with the people it names taken out, with any title said before their name:
    what it asks besides them. "Who is Dean Chen?" names Professor Dean Chen, and "Who is Dean
    Hangen?" names Susan Hangen by her title: neither asks who a school's dean is."""
    text = fold(message).casefold()
    for person in people:
        for token in sorted(name_tokens(_name(person)), key=len, reverse=True):
            text = re.sub(rf"\b(?:(?:dean|professor|prof|dr|chair)\.?\s+)?{re.escape(token)}\b",
                          " ", text)
    return text


def _dean_of_school(title: str) -> tuple[bool, bool] | None:
    """Whether a directory title in a school's listing is that school's dean, and whether
    interim: any Dean title but an assistant, associate or vice dean's ("Dean of Contemporary
    Arts" is how the directory still titles the School of Arts, Humanities, and Education's
    dean, by the Provost's list of deans)."""
    found = _DEAN_TITLE.search(title.split("/")[0])
    if not found or found.group(1):
        return None
    return True, bool(found.group(2))


def leaders(directory: Sequence[dict[str, Any]], message: str,
            schools: dict[str, frozenset[str]], identities: Sequence[dict[str, Any]] = (),
            ) -> list[tuple[str, dict[str, Any] | None]]:
    """Who leads what a leadership question asks about, by directory titles: each school's
    dean (the school named, by a whole name its identity has or else by its words, else every
    school), a permanent Dean before an Interim one for the same school; for a field ("chair of
    nursing"), every title naming the field with a leadership word, else that no title does and
    the dean of the school its faculty belong to. Lines with the directory entry they are
    about."""
    lines: list[tuple[str, dict[str, Any] | None]] = []
    named = schools_by_name(message, identities, list(schools))
    fields = fields_asked(message, directory) - {w for t in schools.values() for w in t
                                                 if len(w) <= 4} - {
        w for name, _ in named for w in name_tokens(name)}
    school = None if named else school_asked(message, schools)

    def deans(of: str) -> list[tuple[str, dict[str, Any] | None]]:
        found = [(e, kind) for e in directory if _school(e.get("school")) == of
                 and "retired" not in str(e.get("school") or "").casefold()
                 and (kind := _dean_of_school(str(e.get("title") or "")))]
        permanent = [e for e, (_, interim) in found if not interim]
        out: list[tuple[str, dict[str, Any] | None]] = []
        for entry, (_, interim) in found:
            if interim and permanent:
                out.append((f"{_name(entry)} is also listed in the directory, as {entry['title']}; "
                            f"an interim appointment is temporary, and the directory lists "
                            f"{_name(permanent[0])} with the title Dean.", entry))
        lead = permanent or [e for e, _ in found]
        if not lead:
            titled = [e for e in directory if _school(e.get("school")) == of
                      and re.search(r"\bdean\b", str(e.get("title") or ""), re.I)
                      and "retired" not in str(e.get("school") or "").casefold()]
            return [(f"The directory lists no one with the title Dean of the {of}.", None),
                    *[(f"{_name(e)} has the directory title {e['title']}.", e) for e in titled]]
        heads: list[tuple[str, dict[str, Any] | None]] = [
            (f"The dean of the {of} is {_name(e)} ({e['title']}).", e) for e in lead]
        return heads + out
    dean_asked = bool(re.search(r"\bdeans?\b", fold(message).casefold()))
    if named and dean_asked:
        for name, named_schools in named:
            # The name the question used is said to be another name of the school(s) it stands
            # for, so a page under that name is read as about them.
            if name.casefold() not in {s.casefold() for s in named_schools}:
                lines.append((f'"{name}" is another name the college\'s records give '
                              + (f"the {named_schools[0]}." if len(named_schools) == 1 else
                                 f"{len(named_schools)} schools: "
                                 + " and ".join(f"the {s}" for s in named_schools) + "."), None))
            for each in named_schools:
                lines.extend(deans(each))
    elif school and dean_asked:
        lines.extend(deans(school))
    listed = {id(entry) for _, entry in lines if entry is not None}

    def leading_in(field: str) -> list[dict[str, Any]]:
        return [e for e in directory if field in name_tokens(str(e.get("title") or ""))
                and "retired" not in str(e.get("school") or "").casefold()
                and _LEAD_TITLE.search(str(e.get("title") or ""))]
    # A field some title leads ("nursing") is what was asked; others the question only mentions
    # ("clinical placements") are not reported as having no chair.
    if any(leading_in(field) for field in fields):
        fields = {field for field in fields if leading_in(field)}
    for field in sorted(fields):
        titled = [e for e in directory if field in name_tokens(str(e.get("title") or ""))
                  and "retired" not in str(e.get("school") or "").casefold()]
        leading = [e for e in titled if _LEAD_TITLE.search(str(e.get("title") or ""))
                   and id(e) not in listed]
        if dean_asked and (school or named):
            # A school's dean asked: only its own people lead its fields, not an office's staff
            # whose title shares a word ("Director of ... Business Partnerships" is not the
            # business school's; fix 4, 11-04, once staff titles were read).
            asked_schools = {school} if school else {s for _, group in named for s in group}
            leading = [e for e in leading if _school(e.get("school")) in asked_schools]
        for entry in leading:
            lines.append((f"{_name(entry)} leads in {field} by their directory title: "
                          f"{entry['title']}.", entry))
        if not leading and not ((school or named) and dean_asked):
            lines.append((f"The directory lists no chair, head, convener, director or dean for "
                          f"{field}.", None))
            home = Counter(_school(e.get("school")) for e in titled if e.get("school"))
            if home:
                of = home.most_common(1)[0][0]
                lines.append((f"The directory's {field} faculty are in the {of}.", None))
                lines.extend(deans(of))
    if not lines and dean_asked:
        for of in ([school] if school else sorted(schools)):
            lines.extend(deans(of))
    return lines


# Who teaches a subject by their profiles' own courses and interests ("who teaches US history"):
# each question word weighted by how rare it is across the profiles, as for calendar topics.
TEACHES_SHARE = 0.6
MAX_TEACHERS = 8
_TEACH_FILLER = discovery_tokens(
    "who teaches teach teaching taught professor professors prof faculty does do is are at "
    "ramapo class classes course courses subject any someone anyone looking for my senior "
    "project mentor want need which the a an in on of to i me can could would like know about "
    "research researches studies study works work interested interests field area best "
    "expert experts good great quote students actually instructor instructors section sections "
    "runs run whos who's give get help")


_TEACH_ASK = re.compile(r"\bteach\w*|\btaught\b|\bclass(?:es)?\b|\bcourses?\b|\binstructors?\b")
_RESEARCH_ASK = re.compile(r"\bresearch\w*|\bstud(?:y|ies)\b|\bworks? on\b|\bexperts?\b|"
                           r"\bspeciali\w*|\binterest\w*")


def _profile_fields(message: str) -> tuple[str, ...]:
    """The profile fields a "who teaches or researches" question is matched on: what people
    teach for a teaching question, what they research for a research one ("I collaborate with
    Computer Science faculty" in a biologist's research is not teaching computer science)."""
    text = fold(message).casefold()
    teaching, research = bool(_TEACH_ASK.search(text)), bool(_RESEARCH_ASK.search(text))
    if teaching and not research:
        return ("courses", "teaching_interests", "title")
    if research and not teaching:
        return ("research_interests", "teaching_interests", "title")
    return ("courses", "teaching_interests", "research_interests", "title")


def _profile_words(entry: dict[str, Any],
                   fields: tuple[str, ...] = ("courses", "teaching_interests",
                                              "research_interests", "title")) -> frozenset[str]:
    return name_tokens(" ".join(str(x) for key in fields
                                for x in (entry.get(key) or [] if key != "title"
                                          else [entry.get("title") or ""])).replace(".", ""))


def teachers(directory: Sequence[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """The people whose profile courses, interests or title hold most of what a "who teaches or
    researches X" question names, weighted by rarity across the directory; at most
    MAX_TEACHERS, best first."""
    import math
    words = set(name_tokens(message.replace(".", ""))) - _TEACH_FILLER - _QUESTION_WORDS
    words = {w for w in words if len(w) > 1}
    if not words:
        return []
    fields = _profile_fields(message)
    abouts = [(_profile_words(e, fields), e) for e in directory
              if "retired" not in str(e.get("school") or "").casefold()]

    def holds(word: str, about: frozenset[str]) -> bool:
        # A short form names its word ("intro" introduction, "chem" chemistry, "gen" general).
        return word in about or (len(word) >= 3 and any(a.startswith(word) for a in about))
    weight = {w: math.log((len(abouts) + 1) / (sum(holds(w, a) for a, _ in abouts) + 0.5))
              for w in words}
    total = sum(weight.values()) or 1.0
    scored = sorted(((sum(weight[w] for w in words if holds(w, a)) / total, e)
                     for a, e in abouts), key=lambda pair: -pair[0])
    found = [e for share, e in scored if share >= TEACHES_SHARE]
    # A subject of two or more words ("computer science", "data structures") is the whole
    # phrase when any profile has it: the rare word alone matched "Computer Music".
    phrase = _topic_phrase(message)
    if len(phrase.split()) >= 2:
        whole = [e for e in found if f" {phrase} " in _profile_text(e, fields)]
        found = whole or found
    return found[:MAX_TEACHERS]


# Who teaches a course a question names, by its code or its catalog title ("who teaches intro
# to psych", "which professors teach organic chemistry 2"): every faculty profile that lists the
# course, by its code or by its title, is code's list. The writer once chose, and listed all 12
# psychology faculty and all 7 chemistry faculty for one course each (fix check 11-04), and
# named the profile listing BIOL 101 but not the one listing Introduction to Biology (11-04
# blind run).
_WHO_TEACHES = re.compile(
    r"\b(?:who|which|what|any)\b[^?.!]*\b(?:teach(?:es|ing)?|taught|instructors?)\b"
    r"|\b(?:professors?|profs?|teachers?|instructors?)\s+(?:for|of|in)\b"
    r"|\bteach(?:es|ing)?\s+(?:the\s+)?[a-z]{3,4}\s?\d{3}\b")
_COURSE_CODE = re.compile(r"\b([A-Z]{3,4})\s?-?\s?(\d{3})\b")
_ASKED_CODE = re.compile(r"\b([a-z]{3,4})\s?-?\s?(\d{3})\b", re.IGNORECASE)
# The number in a series a title ends with ("Calculus I", "Organic Chemistry II"); a profile's
# "Calculus l" is Calculus I.
_SERIES = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "l": 1}
_TITLE_FILLER = frozenset("to of and the in for a an with on".split())
# A question's words that ask who teaches, not which course ("who teaches intro to psych").
_COURSE_ASK_WORDS = frozenset("""
who whos who's whose teaches teach teaching taught professor professors prof profs instructor
instructors teacher teachers faculty does do did is are was were at ramapo college class classes
course courses which what any anyone someone best good great easiest easy hardest hard can could
should would i me my we take taking took section sections this next last semester term usually
normally currently right now here there it its that s
""".split())
_COURSE_PART = frozenset("lecture lab laboratory recitation".split())
# Catalog titles that name no course of their own subject.
_GENERIC_TITLE = re.compile(r"\b(?:transfer elective|independent study|special topics?|"
                            r"internship|co-?op)\b", re.IGNORECASE)


def asks_who_teaches(message: str) -> bool:
    """Whether a question asks who teaches something ("who teaches intro to psych", "the
    professors for calc 1")."""
    return bool(_WHO_TEACHES.search(fold(message).casefold()))


def course_key(title: str) -> tuple[tuple[str, ...], frozenset[int]]:
    """A course title as its words and its numbers in a series, however a catalog or a profile
    writes it: "ORGANIC CHEMISTRY II LECTURE", "Organic Chemistry II (lec & lab)" and "Organic
    Chemistry II Lab" are all (("organic", "chemistry"), {2}); "Fundamentals of Chemistry I and
    II Lecture and Laboratory" is (("fundamentals", "chemistry"), {1, 2}); "MUSI 255 - MUSIC,
    HISTORY AND CULTURE (https://...)" is (("music", "history", "culture"), {})."""
    text = re.sub(r"\(?https?://\S+\)?", " ", fold(title))
    text = _COURSE_CODE.sub(" ", re.sub(r"\([^)]*\)", " ", text)).casefold()
    text = re.sub(r"^\s*(?:undergraduate|graduate|for the [^:]{0,40})\s*:\s*", " ", text)
    text = re.sub(r"-\s*(?:msac|mba)\b", " ", text.replace("&", " and "))
    words = re.findall(r"[a-z0-9]+", text)
    while words and words[-1] in _COURSE_PART | {"and"}:
        words.pop()
    numbers: set[int] = set()
    while len(words) > 1:
        last = words[-1]
        if last in _SERIES or (last.isdigit() and len(last) == 1):
            numbers.add(_SERIES.get(last) or int(last))
            words.pop()
        elif last == "and" and numbers and len(words) > 2 and (
                words[-2] in _SERIES or (words[-2].isdigit() and len(words[-2]) == 1)):
            words.pop()
        else:
            break
    return tuple(w for w in words if w not in _TITLE_FILLER), frozenset(numbers)


def courses_named(message: str, titles: dict[str, str]) -> list[str]:
    """The catalog courses (code to title) a question names: by code ("PSYC 101", "psyc101");
    else by a title whose every word it says, in full or by its first three letters or more
    ("intro" for introduction, "chem" for chemistry), with the title's number in a series when
    the question gives one ("2" or "II"); the titles with the most words said, every course with
    such a title (a series asked without its number is the whole series)."""
    codes = sorted({f"{a.upper()} {b}" for a, b in _ASKED_CODE.findall(message)} & set(titles))
    if codes:
        return codes
    tokens = re.findall(r"[a-z0-9]+", fold(message).casefold())
    words = [w for w in tokens if w not in _TITLE_FILLER and w not in _COURSE_ASK_WORDS]
    # A number in a series follows the title's words ("chemistry 2", "calc i"); "can i" is not
    # Calculus I.
    numbers = {_SERIES.get(w) or int(w) for k, w in enumerate(tokens)
               if k and (w in _SERIES or (w.isdigit() and len(w) == 1))
               and tokens[k - 1] not in _TITLE_FILLER | _COURSE_ASK_WORDS}

    def said(word: str) -> bool:
        return any(q == word or (len(q) >= 3 and not q.isdigit() and word.startswith(q))
                   for q in words)
    best, chosen = 0, []
    for code, title in titles.items():
        base, series = course_key(title)
        if not base or _GENERIC_TITLE.search(title) or not all(said(w) for w in base):
            continue
        if numbers and series and not series & numbers:
            continue
        if len(base) > best:
            best, chosen = len(base), [code]
        elif len(base) == best:
            chosen.append(code)
    return sorted(chosen)


def course_teachers(directory: Sequence[dict[str, Any]], codes: Sequence[str],
                    titles: dict[str, str]) -> list[dict[str, Any]]:
    """Everyone whose faculty profile lists one of the courses, by its code or by its title (a
    profile's title with no number in a series names the whole series), in the directory's
    order. A retired professor's profile still lists what they taught, so retired listings are
    left out when anyone else lists the course (11-04 live check: "who teaches intro to psych"
    named a retired professor); when only retired ones do, they are kept and said as retired
    (course_teachers_text)."""
    keys = {course_key(titles[code]) for code in codes if code in titles}
    found = []
    for entry in directory:
        for course in entry.get("courses") or []:
            text = str(course)
            listed = {f"{a} {b}" for a, b in _COURSE_CODE.findall(text)}
            base, series = course_key(text)
            if listed & set(codes) or any(
                    base == b and (not series or bool(series & n)) for b, n in keys if b):
                found.append(entry)
                break
    active = [e for e in found if not _retired(e)]
    return active or found


def _retired(entry: dict[str, Any]) -> bool:
    """Whether the directory or the profile lists a person as retired."""
    return any("retired" in str(entry.get(key) or "").casefold() for key in ("school", "title"))


def catalog_title(title: str) -> str:
    """A catalog title in capitals as a title is written ("INTRODUCTION TO PSYCHOLOGY" is
    "Introduction to Psychology", "ORGANIC CHEMISTRY II LECTURE" "Organic Chemistry II
    Lecture")."""
    words = title.split()
    out = []
    for k, word in enumerate(words):
        low = word.casefold()
        out.append(word.upper() if low.strip(":,") in _SERIES and low.strip(":,") != "l" else
                   low if k and low in _TITLE_FILLER else word[:1].upper() + word[1:].casefold())
    return " ".join(out)


def course_teachers_text(codes: Sequence[str], titles: dict[str, str],
                         people: Sequence[dict[str, Any]], asks: Sequence[str] = ()) -> str:
    """Code's answer to who teaches the courses: each person whose profile lists one, with their
    directory title and email and the fields asked [their profile], and that a profile lists the
    courses a professor teaches, not this term's sections."""
    named = "; ".join(f"{code} {literal(catalog_title(titles[code]))}" for code in codes)
    if not people:
        return f"No faculty profile lists {named} among its courses."
    lines = [f"Faculty profiles that list {named} among the courses they teach:"]
    for entry in people:
        bits = [literal(str(entry.get("name") or ""))]
        if entry.get("title"):
            bits.append(literal(str(entry["title"])))
        if ("retired" in str(entry.get("school") or "").casefold()
                and "retired" not in str(entry.get("title") or "").casefold()):
            bits.append("listed as retired in the directory")
        if entry.get("email"):
            bits.append(str(entry["email"]))
        bits.extend(asked_bits(entry, asks))
        lines.append("- " + ", ".join(b for b in bits if b) + f" [{entry['n']}]")
    lines.append("A profile lists the courses a professor teaches; it doesn't say who teaches "
                 "which section this term.")
    return "\n".join(lines)


def _plain_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(text).casefold().replace(".", ""))


def _topic_phrase(message: str) -> str:
    """The longest run of a question's subject words, as a phrase ("computer science")."""
    runs: list[list[str]] = [[]]
    for word in _plain_words(message):
        if len(word) > 1 and word not in _TEACH_FILLER | _QUESTION_WORDS | _DISCOVERY_SKIP:
            runs[-1].append(word)
        elif runs[-1]:
            runs.append([])
    longest: list[str] = []
    for run in runs:
        longest = run if len(run) > len(longest) else longest
    return " ".join(longest)


_DISCOVERY_SKIP = frozenset({"a", "an", "the", "of", "for", "and"})


def _profile_text(entry: dict[str, Any], fields: tuple[str, ...]) -> str:
    """A profile's fields as plain words, each item apart ("Data Structures | Calculus II")."""
    items = [str(x) for key in fields
             for x in (entry.get(key) or [] if key != "title" else [entry.get("title") or ""])]
    return " " + " | ".join(" ".join(_plain_words(item)) for item in items) + " "


# A question asking for a program's or a field's faculty as a list ("list the bio faculty", "all
# the professors in computer science", "who teaches biology"): code lists everyone the catalog
# links to the program, from the graph (11-03: the reply listed the 4 search happened to find
# of Biology's 12, and none of Computer Science's 9).
_FACULTY_LIST = re.compile(
    r"\b(?:list|all|every|everyone|who are the|whos are the|faculty|professors|profs|"
    r"teachers|instructors|who teach(?:es)?)\b")
# One person asked ("the best professor for", "is professor x"): not a list.
_ONE_PERSON = re.compile(r"\b(?:best|easiest|hardest|worst|favou?rite|a good)\s+"
                         r"(?:professor|prof|teacher|instructor)\b")


def asks_faculty_list(message: str) -> bool:
    text = fold(message).casefold()
    return asks_people(text) and bool(_FACULTY_LIST.search(text)) \
        and not _ONE_PERSON.search(text)


# Words of a clause that asks only how to reach or find the people asked about ("and what is
# their email and office number", "and where can I find them").
_FIELD_CLAUSE_WORDS = frozenset("""
and also plus what whats what's is are their his her them him they where wheres where's can could
do does would i find reach contact email e-mail emails phone phones number numbers office offices
room rooms hours located location the a an info information details me to get in touch with go
""".split())


def without_field_asks(question: str) -> str:
    """The question less each clause that asks only for the people's contact fields, which the
    faculty lines give (faculty_text): "who is the psychology convener and what is their email
    and office number" asks nothing more than the convener's line (fix check 11-04)."""
    parts = re.split(r"(?=\b(?:and|also|plus)\b)", question)
    kept = [parts[0]]
    for part in parts[1:]:
        words = re.findall(r"[a-z'’-]+", fold(part).casefold())
        if not (words and set(words) <= _FIELD_CLAUSE_WORDS and fields_named(part)):
            kept.append(part)
    return "".join(kept)


# What a faculty line adds when the question names it ("who is the psychology convener and what
# is their email and office number"): the convener line gave the email only, and the reply then
# said it had no office number, though G-273 is in her directory entry (fix check 11-04).
_LINE_FIELDS = {"office": "office", "phone": "phone", "office_hours": "office hours"}


def asked_bits(entry: dict[str, Any], asks: Sequence[str]) -> list[str]:
    """The office, phone and office hours of a directory entry the question names (`asks`), each
    as the directory gives it, or "no office listed" when it gives none."""
    bits = []
    for key, label in _LINE_FIELDS.items():
        if key not in asks:
            continue
        value = entry.get(key)
        shown = "; ".join(re.sub(r"^\s*office hours\s*:\s*", "", str(v), flags=re.I).strip()
                          for v in (value if isinstance(value, list) else [value]) if v)
        bits.append(f"{label} {literal(shown).rstrip('.')}" if shown else f"no {label} listed")
    return bits


def faculty_text(subject: str, programs: Sequence[dict[str, Any]],
                 people: dict[str, dict[str, Any]], conveners_only: bool = False,
                 asks: Sequence[str] = ()) -> str | None:
    """Code's list of a subject's faculty, from the catalog's links (EntityFacts.program_people):
    how many the catalog lists [its page], then each person once, conveners first, with their
    directory title and email, and the office, phone and office hours the question names (`asks`,
    from fields_named) [their profile, else the catalog page]. `programs` are the subject's
    levels as read, each with its "n"; `people` each listed person's directory entry by id, with
    its "n"; `conveners_only` lists only the conveners (who leads the program). None when the
    catalog links no one."""
    order: list[str] = []
    convener_of: dict[str, list[str]] = {}
    retired: set[str] = set()
    for program in programs:
        for pid in program.get("conveners") or []:
            convener_of.setdefault(pid, []).append(str(program["name"]))
        retired.update(program.get("retired") or [])
    for program in programs:
        for pid in [*(program.get("conveners") or []),
                    *([] if conveners_only else program.get("faculty") or [])]:
            if pid in people and pid not in order:
                order.append(pid)
    order.sort(key=lambda pid: pid not in convener_of)
    if not order:
        return None
    marks = "".join(f"[{n}]" for n in dict.fromkeys(p["n"] for p in programs if p.get("n")))
    names = ", ".join(literal(str(p["name"])) for p in programs)
    head = (f"The college catalog names the program convener{'s' if len(order) != 1 else ''}"
            f" of {literal(subject)} ({names}) {marks}:" if conveners_only else
            f"The college catalog lists {len(order)} "
            f"{'person' if len(order) == 1 else 'people'} as faculty of {literal(subject)}"
            f" ({names}) {marks}:")
    lines = []
    for pid in order:
        entry = people[pid]
        bits = [literal(str(entry.get("name") or ""))]
        if pid in convener_of:
            of = convener_of[pid]
            bits.append("program convener" if len(of) == len(programs) else
                        "convener of " + ", ".join(literal(n) for n in of))
        if entry.get("title"):
            bits.append(literal(str(entry["title"])))
        if pid in retired:
            bits.append("listed as retired in the directory")
        if entry.get("email"):
            bits.append(str(entry["email"]))
        bits.extend(asked_bits(entry, asks))
        lines.append("- " + ", ".join(b for b in bits if b) + f" [{entry['n']}]")
    return "\n".join([head, *lines])


# Who leads a program, not a school: its chair, head or convener (the catalog's word). A dean
# leads a school, and is the directory's (leaders).
_PROGRAM_LEAD = re.compile(r"\b(?:chair(?:s|person|man|woman)?|conveners?|heads?|in charge|"
                           r"who runs|runs the|coordinators?|program directors?)\b")


def asks_program_lead(message: str) -> bool:
    text = fold(message).casefold()
    return bool(_PROGRAM_LEAD.search(text)) and not re.search(r"\bdeans?\b", text)
