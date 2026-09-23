"""Course subjects a question names: "CS", "Comp Sci" or "computer science" is CMPS.

The release's `course-subjects` artifact lists every subject in its catalog: the
code in front of its courses, the catalog department's name and curated short
forms. Course search resolves a mention against that list and nothing else, so
no subject is inferred from course titles.

- A code matches only as written in capitals: "READ" is the subject, "read" is not.
- Names and short forms match whole words in any case, longest first, so "art
  history" means ARHT before "art" can mean ARTS.
- A name the catalog gives several codes ("Interdisciplinary Studies") means all of them.

In profile lookup a subject answers to its code only (reviewed September 23, 2026);
its name and short forms mean courses here, in course search.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Words that ask for courses without narrowing which ones.
GENERIC = frozenset({"course", "courses", "class", "classes", "subject", "subjects"})
COURSE_CODE = re.compile(r"([A-Z]{2,6}) \S+")


@dataclass(frozen=True)
class SubjectMention:
    code: str
    subject: str
    matched: str
    basis: str  # "code", "catalog_name" or "search_term"


def course_subject(code: Any) -> str | None:
    """The subject code of a catalog course code such as "CMPS 147"."""
    match = COURSE_CODE.fullmatch(code) if isinstance(code, str) else None
    return match.group(1) if match else None


def resolve_subjects(query: str, payload: Any) -> tuple[list[SubjectMention], str]:
    """The subjects a course question names, and its remaining words."""
    subjects = payload.get("subjects") if isinstance(payload, dict) else None
    if not isinstance(subjects, list):
        return [], query
    codes: dict[str, str] = {}
    phrases: dict[tuple[str, ...], list[tuple[str, str, str]]] = {}
    for subject in subjects:
        if not isinstance(subject, dict) or not isinstance(subject.get("code"), str):
            continue
        code = subject["code"]
        display = str(subject.get("display_name") or code)
        codes[code] = display
        named = [(subject.get("name"), "catalog_name"),
                 *((term, "search_term") for term in subject.get("search_terms") or [])]
        for text, basis in named:
            words = tuple(re.findall(r"\w+", text.casefold())) if isinstance(text, str) else ()
            if words and (code, display, basis) not in phrases.get(words, []):
                phrases.setdefault(words, []).append((code, display, basis))
    tokens = re.findall(r"\w+", query)
    lowered = [token.casefold() for token in tokens]
    longest = max((len(words) for words in phrases), default=0)
    mentions: list[SubjectMention] = []
    used: set[int] = set()
    index = 0
    while index < len(tokens):
        if tokens[index] in codes:
            mentions.append(SubjectMention(tokens[index], codes[tokens[index]], tokens[index],
                                           "code"))
            used.add(index)
            index += 1
            continue
        for size in range(min(longest, len(tokens) - index), 0, -1):
            matches = phrases.get(tuple(lowered[index:index + size]))
            if matches:
                matched = " ".join(tokens[index:index + size])
                mentions.extend(SubjectMention(code, display, matched, basis)
                                for code, display, basis in matches)
                used.update(range(index, index + size))
                index += size
                break
        else:
            index += 1
    unique: list[SubjectMention] = []
    for mention in mentions:
        if all(mention.code != kept.code for kept in unique):
            unique.append(mention)
    remaining = " ".join(token for position, token in enumerate(tokens)
                         if position not in used and token.casefold() not in GENERIC)
    return unique, remaining
