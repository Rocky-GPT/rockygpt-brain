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
    return found[:MAX_PEOPLE]


def by_meaning(scores: Sequence[tuple[str, float]], names: dict[str, str]) -> list[dict[str, Any]]:
    """The people whose cards best match the question: above the floor, near the best."""
    if not scores or scores[0][1] < PERSON_FLOOR:
        return []
    return [{"id": entity_id, "name": names[entity_id]} for entity_id, score in scores
            if score >= max(PERSON_FLOOR, scores[0][1] - PERSON_NEAR) and entity_id in names
            ][:MAX_PEOPLE]


def person_evidence(facts: dict[str, Any], n: int) -> dict[str, Any]:
    """What the reply is given for one person: the directory's and profile's published facts."""
    prop = facts["properties"][0]
    value = prop["values"][0]["value"] if prop["values"] else {"name": facts["entity"]["name"]}
    return {"n": n, **{k: v for k, v in value.items() if v not in (None, [], "")}}
