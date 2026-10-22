"""Majors, minors and certificates: which program a question names, and its requirements.

Code finds the program and writes out its requirements from the catalog's own rules, exactly as
published (which courses, how many of them, the catalog's notes); the model only words the reply.
A program is named by its catalog name without its level ("Computer Science" of "Computer Science
BS"), and the question's level words ("minor", "master's", "4+1") choose among the levels. With no
level word, the undergraduate major is the program and the other levels are named beside it.
"""

import re
from collections.abc import Sequence
from typing import Any

from rockygpt_brain.retrieval.entity_facts import discovery_tokens

# A catalog name ends in its level: "Psychology BA", "Accounting 4+1", "Data Analyst - Graduate
# Certificate". The core is the rest.
_LEVEL_SUFFIX = re.compile(
    r"\s*(?:-\s*)?(?:graduate certificate|certificate|minor|4\+1|accelerated bsn|generic bsn|"
    r"rn to bsn|bsn|msn|dnp|bsw|msw|mba|mfa|mpp|ba|bs|ms|ma)\s*$", re.IGNORECASE)
# The words of a question that choose a program's level, by the catalog's program kind and degree.
_LEVEL_WORDS = {
    "minor": re.compile(r"\bminor(?:s|ing)?\b"),
    "certificate": re.compile(r"\bcert(?:ificate)?s?\b"),
    "special": re.compile(r"4\s*\+\s*1|\bfive[- ]year\b|\b5[- ]year\b"),
    "graduate": re.compile(
        r"\bmaster'?s?\b|\bgrad(?:uate)?\b|\bm\.?s\.?\b|\bm\.?a\.?\b|\bmba\b|\bmsw\b|\bmfa\b|"
        r"\bmpp\b|\bmsn\b|\bdnp\b|\bdoctor"),
    "major": re.compile(r"\bmajor(?:s|ing)?\b|\bbachelor'?s?\b|\bb\.?a\.?\b|\bb\.?s\.?\b|\bbsn\b|"
                        r"\bbsw\b|\bundergrad"),
}
MAX_PROGRAMS = 3


def program_core(name: str) -> str:
    """A catalog program name without its level: "Computer Science" of "Computer Science BS"."""
    return _LEVEL_SUFFIX.sub("", name).strip()


def program_level(name: str) -> str:
    """The level a catalog name ends in, as a question's words name it: minor, certificate,
    special (4+1), graduate (MS, MBA, DNP …), major (BA, BS, BSN …), or "" for the rest."""
    found = _LEVEL_SUFFIX.search(name)
    suffix = found.group(0).strip(" -").casefold() if found else ""
    if suffix == "minor":
        return "minor"
    if suffix.endswith("certificate"):
        return "certificate"
    if suffix == "4+1":
        return "special"
    if suffix in {"ms", "ma", "mba", "mfa", "mpp", "msw", "msn", "dnp"}:
        return "graduate"
    return "major" if suffix else ""


def asked_levels(message: str) -> set[str]:
    text = message.casefold()
    return {level for level, words in _LEVEL_WORDS.items() if words.search(text)}


def find_programs(programs: Sequence[dict[str, Any]], message: str) -> list[dict[str, Any]]:
    """The programs (identities: {"id", "name", …}) a message names, best first, at most
    MAX_PROGRAMS. A program is named when every word of its core name is a word of the message
    (the longest such core wins: "Data Science" over "Science"). Among its levels the asked ones
    are kept; with none asked, the undergraduate major."""
    words = discovery_tokens(message)
    named = [(program, core) for program in programs
             if (core := discovery_tokens(program_core(str(program["name"]))))
             and core <= words]
    if not named:
        return []
    widest = max(len(core) for _, core in named)
    found = [program for program, core in named if len(core) == widest]
    levels = asked_levels(message)
    if levels:
        chosen = [p for p in found if program_level(str(p["name"])) in levels] or found
    else:
        chosen = [p for p in found if program_level(str(p["name"])) == "major"] or found
    return sorted(chosen, key=lambda p: str(p["name"]))[:MAX_PROGRAMS]


def also_published(programs: Sequence[dict[str, Any]],
                   chosen: Sequence[dict[str, Any]]) -> list[str]:
    """The other levels of the chosen programs' core names ("Computer Science Minor, MS")."""
    cores = {program_core(str(p["name"])).casefold() for p in chosen}
    ids = {p["id"] for p in chosen}
    return sorted(str(p["name"]) for p in programs
                  if p["id"] not in ids and program_core(str(p["name"])).casefold() in cores)


_CODE = re.compile(r"^[A-Z]{2,6} \d{1,3}[A-Z]?$")


def _course(course: dict[str, Any]) -> str:
    code = str(course.get("code") or "").strip()
    name = " ".join(str(course.get("name") or "").split()).rstrip(":")
    if not name and not _CODE.match(code):
        return "an unnamed catalog entry"  # The catalog lists an internal id here, not a course.
    return f"{code} {name}".strip() if name else code


def program_evidence(facts: dict[str, Any], n: int, others: Sequence[str]) -> dict[str, Any]:
    """What the reply is given for one program: who it is (from its catalog row), its
    requirements written out by code, and the program's other published levels."""
    props = {prop["key"]: prop for prop in facts["properties"]}
    program = props["program"]["values"][0]["value"]
    groups = [value["value"] for value in props.get("requirements", {}).get("values", [])]
    description = str(program.get("description") or "")
    return {"n": n, "program": program.get("name"), "degree": program.get("degree"),
            "school": program.get("school"), "catalog_url": program.get("catalog_url"),
            **({"description": description[:800]} if description else {}),
            "requirements": render_requirements(groups) if groups else None,
            **({"also_published": list(others)} if others else {})}


def _how_many(rule: dict[str, Any]) -> str:
    choose = rule.get("choose") or {}
    if choose.get("all"):
        return "all of"
    if isinstance(choose.get("at_least"), int):
        return f"at least {choose['at_least']} of"
    if isinstance(choose.get("minimum_credits"), int):
        return f"at least {choose['minimum_credits']} credits from"
    return ""


def render_rule(rule: dict[str, Any], depth: int = 0) -> list[str]:
    """A published rule as lines: its name and how many of its items it asks for, its note, its
    courses (an item's courses joined by its own and/or), then its sub-rules, each indented."""
    pad = "  " * depth
    head = " ".join(part for part in (str(rule.get("name") or "").strip(), _how_many(rule))
                    if part)
    lines = [f"{pad}{head}:"] if head else []
    if isinstance(rule.get("credits"), int | float):
        lines.append(f"{pad}  (credits: {rule['credits']:g})")
    if rule.get("note"):
        lines.append(f"{pad}  Note: {' '.join(str(rule['note']).split())}")
    for item in rule.get("items") or []:
        courses = [c for c in item.get("courses") or [] if isinstance(c, dict)]
        if not courses:
            continue
        joiner = " or " if item.get("logic") == "or" and len(courses) > 1 else " and "
        lines.append(f"{pad}  - {joiner.join(_course(c) for c in courses)}")
    for sub in rule.get("sub_rules") or []:
        if isinstance(sub, dict):
            lines += render_rule(sub, depth + 1)
    return lines


def render_requirements(groups: Sequence[dict[str, Any]]) -> str:
    """A program's requirement groups, in catalog order, as the lines code writes for them."""
    lines: list[str] = []
    for group in groups:
        lines.append(f"{group.get('label') or 'Requirements'}")
        if group.get("note"):
            lines.append(f"  Note: {' '.join(str(group['note']).split())}")
        if isinstance(group.get("rule"), dict):
            lines += render_rule(group["rule"], 1)
        for course in group.get("course_list") or []:
            if isinstance(course, dict):
                lines.append(f"  - {_course(course)}")
    return "\n".join(lines)


# A course code as people type it: "CMPS 147", "cmps147", "CMPS-147", "MATH 100T", "PATHCA 1".
_TYPED_CODE = re.compile(r"\b([A-Za-z]{2,6})\s*-?\s*(\d{1,3}[A-Za-z]?)\b")
MAX_COURSES = 6


def course_codes(message: str, known: set[str]) -> list[str]:
    """The catalog course codes a message names, in its order: a typed code counts only when it
    is exactly a published catalog code, so "ASB 312" (a room) and "room 147" are not courses."""
    found: list[str] = []
    for subject, number in _TYPED_CODE.findall(message):
        code = f"{subject.upper()} {number.upper()}"
        if code in known and code not in found:
            found.append(code)
    return found[:MAX_COURSES]


def render_requisites(requisites: Sequence[dict[str, Any]]) -> str:
    """A course's published prerequisites and corequisites as lines, each section with its rule,
    the courses (joined by the item's own and/or) and the catalog's notes, as published."""
    lines: list[str] = []
    for section in requisites:
        if not isinstance(section, dict):
            continue
        lines.append(str(section.get("section") or "Requisite"))
        found = section.get("rule")
        rule: dict[str, Any] = found if isinstance(found, dict) else {}
        head = str(rule.get("name") or "").strip()
        condition = {"completedAllOf": "all of", "completedAnyOf": "one of"}.get(
            str(rule.get("condition") or ""), "")
        grade = rule.get("minimumGrade") or rule.get("minimum_grade")
        if head or condition:
            lines.append(f"  {head} {condition}".rstrip() + ":")
        if grade:
            lines.append(f"  (minimum grade: {grade})")
        for item in rule.get("items") or []:
            codes = [c for c in item.get("codes") or [] if isinstance(c, dict)]
            if codes:
                joiner = " or " if item.get("logic") == "or" and len(codes) > 1 else " and "
                lines.append(f"  - {joiner.join(_course(c) for c in codes)}")
        if rule.get("note"):
            lines.append(f"  Note: {' '.join(str(rule['note']).split())}")
    return "\n".join(lines)


def course_evidence(facts: dict[str, Any], n: int) -> dict[str, Any]:
    """What the reply is given for one course, from its catalog record."""
    course = facts["properties"][0]["values"][0]["value"]
    requisites = course.get("requisites") or []
    return {"n": n, "course": f"{course.get('code')} {course.get('name') or ''}".strip(),
            "credits": course.get("credits"), "school": course.get("school"),
            "description": course.get("description") or None,
            "attributes": course.get("attributes") or [],
            "prerequisites": render_requisites(requisites) if requisites else None,
            "catalog_url": course.get("catalog_url")}
