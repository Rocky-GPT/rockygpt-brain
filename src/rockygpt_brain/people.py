"""Faculty and staff: which people a question names, read through the shared reader.

A person is named by their whole directory name ("Rikki Abzug"), or by a last name that is exactly
one person's ("professor abzug"); a question about who researches or teaches something finds people
by their card's meaning (name, title, school, interests). Naming a person establishes none of their
facts: the directory and their faculty profile do.
"""

import re
from collections.abc import Sequence
from typing import Any

from rockygpt_brain.retrieval.entity_facts import discovery_tokens

MAX_PEOPLE = 4
_PEOPLE_WORDS = re.compile(
    r"\b(?:professors?|prof|profs|dr|doctor|teachers?|instructors?|faculty|lecturers?|"
    r"office hours|who teaches|teaches|chair|advisors?|advisers?|dean|researcher|research(?:es)?"
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


def find_people(people: Sequence[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """The people a message names: every word of a directory name, else a last name that is
    exactly one person's. A hyphenated last name names its person whole ("williams-goldstein")
    even where each half is someone else's too. A last name several people share names all of
    them (the reply asks which one); a two-letter last name ("xu") counts only after a title
    word. At most MAX_PEOPLE."""
    words = discovery_tokens(message) - _TITLES
    shortest = 2 if discovery_tokens(message) & _TITLES else 3
    whole = [p for p in people if (name := discovery_tokens(_name(p)))
             and len(name) >= 2 and name <= words]
    if whole:
        return whole[:MAX_PEOPLE]
    # A last name alone is a person only in a question about one ("is the food good" names no
    # Professor Norflus-Good).
    if not (asks_people(message) or _CONTACT_ASK.search(message.casefold())):
        return []
    owners: dict[frozenset[str], list[dict[str, Any]]] = {}
    for person in people:
        parts = _name(person).split()
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
    if not found and not named:
        return _near_names(people, words)
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
        parts = _name(person).split()
        last = sorted(discovery_tokens(parts[-1]), key=len)[-1:] if parts else []
        for token in last:
            allowed = 1 if len(token) <= 6 else 2
            if len(token) >= 4 and any(
                    abs(len(w) - len(token)) <= allowed and len(w) >= 4
                    and w[0] == token[0] and _distance(w, token) <= allowed for w in words):
                found.append({**person, "near_spelling": True})
    # A first name the question also uses picks among near spellings ("mark howenstien").
    first_named = [p for p in found if _name(p).split()
                   and discovery_tokens(_name(p).split()[0]) & words]
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
