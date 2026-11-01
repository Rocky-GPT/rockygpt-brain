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

from rockygpt_brain.retrieval.entity_facts import discovery_tokens

MAX_PEOPLE = 4
_PEOPLE_WORDS = re.compile(
    r"\b(?:professors?|prof|profs|dr|doctor|teachers?|instructors?|faculty|lecturers?|"
    r"office hours|teach(?:es|ing)?|taught|chair|advisors?|advisers?|dean|researcher|"
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
# chemistry), and common words a question uses for other things.
_NOT_FIELD = discovery_tokens(
    "professor associate assistant adjunct visiting distinguished emeritus emerita retired "
    "interim lecturer instructor librarian senior teaching specialist fellow executive "
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
    text = fold(message).casefold()
    asked = [(key, label) for key, label, words in _ASKED_FIELDS if words.search(text)]
    return asked or [(key, label) for key, label, _ in _ASKED_FIELDS if key in _CONTACT_FIELDS]


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
            else:
                facts.append({"fact": f"Neither the directory nor {name}'s faculty profile "
                                      f"publishes {'an' if label[0] in 'aeiou' else 'a'} "
                                      f"{label}.", "n": n})
    return facts


# A question about who leads a school or a field: its dean, or a department's chair or head.
_LEAD_ASK = re.compile(r"\b(?:deans?|chair(?:s|person|man|woman)?|conveners?|"
                       r"(?:department|dept|program) heads?|head of|in charge of)\b")
_LEAD_TITLE = re.compile(r"\b(?:dean|chair\w*|convener|director|coordinator|head)\b", re.I)
_DEAN_TITLE = re.compile(r"\b(?:(assistant|associate|vice)\s+)?(interim\s+)?dean\b", re.I)


def asks_leadership(message: str) -> bool:
    text = fold(message).casefold()
    return bool(_LEAD_ASK.search(text)) and "dean of students" not in text


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
            schools: dict[str, frozenset[str]]) -> list[tuple[str, dict[str, Any] | None]]:
    """Who leads what a leadership question asks about, by directory titles: each school's
    dean (the school named, else every school), a permanent Dean before an Interim one for the
    same school; for a field ("chair of nursing"), every title naming the field with a
    leadership word, else that no title does and the dean of the school its faculty belong to.
    Lines with the directory entry they are about."""
    lines: list[tuple[str, dict[str, Any] | None]] = []
    fields = fields_asked(message, directory) - {w for t in schools.values() for w in t
                                                 if len(w) <= 4}
    school = school_asked(message, schools)

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
            (f"The dean of the {of}, by the directory, is {_name(e)} (directory title: "
             f"{e['title']}).", e) for e in lead]
        return heads + out
    dean_asked = bool(re.search(r"\bdeans?\b", fold(message).casefold()))
    if school and dean_asked:
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
        for entry in leading:
            lines.append((f"{_name(entry)}'s directory title names {field} with a leadership "
                          f"role: {entry['title']}.", entry))
        if not leading and not (school and dean_asked):
            lines.append((f"No directory title names a chair, head, convener, director or "
                          f"dean for {field}.", None))
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


def _profile_words(entry: dict[str, Any]) -> frozenset[str]:
    return name_tokens(" ".join(str(x) for key in ("courses", "teaching_interests",
                                                   "research_interests", "title")
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
    abouts = [(_profile_words(e), e) for e in directory
              if "retired" not in str(e.get("school") or "").casefold()]

    def holds(word: str, about: frozenset[str]) -> bool:
        # A short form names its word ("intro" introduction, "chem" chemistry, "gen" general).
        return word in about or (len(word) >= 3 and any(a.startswith(word) for a in about))
    weight = {w: math.log((len(abouts) + 1) / (sum(holds(w, a) for a, _ in abouts) + 0.5))
              for w in words}
    total = sum(weight.values()) or 1.0
    scored = sorted(((sum(weight[w] for w in words if holds(w, a)) / total, e)
                     for a, e in abouts), key=lambda pair: -pair[0])
    return [e for share, e in scored if share >= TEACHES_SHARE][:MAX_TEACHERS]
