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
_TOPIC_ASK = re.compile(r"\b(?:who|teach\w*|research\w*|stud(?:y|ies)|experts?|chair|"
                        r"specializ\w*|works? on)\b")


def asks_people(message: str) -> bool:
    return bool(_PEOPLE_WORDS.search(message.casefold()))


def asks_people_topic(message: str) -> bool:
    """Whether a question asks who works on a topic, rather than about a person by name."""
    return asks_people(message) and bool(_TOPIC_ASK.search(message.casefold()))


def find_people(people: Sequence[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """The people a message names: every word of a directory name, else a last name that is
    exactly one person's (a title word before it, or none). At most MAX_PEOPLE."""
    words = discovery_tokens(message) - _TITLES
    whole = [p for p in people if (name := discovery_tokens(str(p["name"])))
             and len(name) >= 2 and name <= words]
    if whole:
        return whole[:MAX_PEOPLE]
    by_last: dict[str, list[dict[str, Any]]] = {}
    for person in people:
        parts = str(person["name"]).replace(",", " ").split()
        if parts:
            last = discovery_tokens(parts[-1])
            for token in last:
                if len(token) >= 3:
                    by_last.setdefault(token, []).append(person)
    found: list[dict[str, Any]] = []
    for token in words:
        matches = by_last.get(token, [])
        if len(matches) == 1 and matches[0] not in found:
            found.append(matches[0])
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
