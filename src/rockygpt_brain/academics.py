"""Majors, minors and certificates: which program a question names, and its requirements.

Code finds the program and writes out its requirements from the catalog's own rules, exactly as
published (which courses, how many of them, the catalog's notes); the model only words the reply.
A program is named by its catalog name without its level ("Computer Science" of "Computer Science
BS"), and the question's level words ("minor", "master's", "4+1") choose among the levels. With no
level word, the undergraduate major is the program and the other levels are named beside it.
"""

import re
from collections.abc import Sequence
from datetime import date
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
    or a short form of it ("math" for Mathematics, "chem" for Chemistry); one whose words all
    belong to a longer program also named is dropped ("Data Science", not "Science"). Each
    subject's level comes from the words beside it ("a biology major and a chem minor"), else
    from the whole message, else the undergraduate major."""
    tokens = re.findall(r"[a-z0-9+]+", message.casefold())
    named: list[tuple[dict[str, Any], frozenset[str], list[int], set[int]]] = []
    for program in programs:
        core = discovery_tokens(program_core(str(program["name"])))
        if not core:
            continue
        places: list[int] = []
        every: set[int] = set()
        for word in core:
            at = [k for k, w in enumerate(tokens) if w == word or (
                w not in _ASKING and _abbreviates(w, word) and len(w) >= 3)]
            if not at:
                break
            places.append(min(at))
            every.update(at)
        else:
            named.append((program, core, places, every))
    if not named:
        return []
    # A shorter name is dropped only when every mention of it is inside the longer one ("just
    # physics" still names Physics beside Engineering Physics).
    kept = [(p, core, places) for p, core, places, every in named
            if not any(core < other and every <= set(o_places)
                       for _, other, o_places, _ in named)]
    whole = asked_levels(message)
    chosen: list[dict[str, Any]] = []
    for core in dict.fromkeys(core for _, core, _ in kept):
        group = [(p, places) for p, c, places in kept if c == core]
        start, end = min(group[0][1]), max(group[0][1])
        beside = " ".join(tokens[max(0, start - 3):end + 4])
        levels = asked_levels(beside) or whole
        pick = ([p for p, _ in group if program_level(str(p["name"])) in levels] if levels
                else [p for p, _ in group if program_level(str(p["name"])) == "major"])
        chosen += pick or [p for p, _ in group]
    return chosen[:MAX_PROGRAMS]


# A program or course found by its card's meaning (the meaning index), not its exact name. On
# 14 sample questions (10-21) academic ones scored 0.47-0.73 against their program and others at
# most 0.30; courses 0.58-0.71 against at most 0.47 for questions about no course.
PROGRAM_FLOOR, COURSE_FLOOR, NEAR = 0.42, 0.55, 0.03


def by_meaning(scores: Sequence[tuple[str, float]], names: dict[str, str], message: str,
               floor: float, limit: int) -> list[dict[str, Any]]:
    """The best scored programs or courses at or above the floor and within NEAR of the best;
    for programs, the question's level words choose among them as they do for a named one."""
    if not scores or scores[0][1] < floor:
        return []
    near = [{"id": entity_id, "name": names[entity_id]} for entity_id, score in scores[:8]
            if score >= max(floor, scores[0][1] - NEAR) and entity_id in names]
    if limit == 1 or not near or program_level(str(near[0]["name"])) == "":
        return near[:limit]
    levels = asked_levels(message)
    chosen = ([p for p in near if program_level(str(p["name"])) in levels] if levels
              else [p for p in near if program_level(str(p["name"])) == "major"])
    return (chosen or near)[:limit]


_ACADEMIC = re.compile(r"\b(?:majors?|minors?|degrees?|programs?|certificates?|courses?|"
                       r"class(?:es)?|credits?|prereq\w*|gen ?eds?|concentrations?)\b")


def asks_academics(message: str) -> bool:
    """Whether a message asks about programs or courses at all (its words, not its meaning)."""
    return bool(_ACADEMIC.search(message.casefold()))


_LISTING_LEVELS = {"major": "majors", "minor": "minors", "graduate": "graduate programs",
                   "certificate": "certificates", "special": "4+1 programs"}


def catalog_listing(programs: Sequence[dict[str, Any]], message: str) -> dict[str, list[str]]:
    """Every published program's name by level (the levels the message asks about, else all),
    so "what majors are there" is answered and a program the catalog does not list is said to
    be missing rather than guessed at."""
    levels = asked_levels(message) or set(_LISTING_LEVELS)
    listing: dict[str, list[str]] = {}
    for program in programs:
        level = program_level(str(program["name"]))
        if level in levels:
            listing.setdefault(_LISTING_LEVELS[level], []).append(str(program["name"]))
    return {key: sorted(names) for key, names in listing.items()}


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


_YEAR = re.compile(r"\b(20\d\d)\b")
# A student's class names how many years ago they were admitted: a junior's plan is the one for
# students admitted two academic years before this one.
_CLASS_YEARS = ((re.compile(r"\bsenior|\bfourth[- ]year"), 3),
                (re.compile(r"\bjunior|\bthird[- ]year"), 2),
                (re.compile(r"\bsophomore|\bsecond[- ]year"), 1))


def current_cohort(today: date) -> str:
    """The admission cohort whose academic year holds today: Fall 2026 from August 2026."""
    return f"Fall {today.year if today.month >= 8 else today.year - 1}"


def choose_plan(plans: Sequence[dict[str, Any]], program: str, message: str,
                today: date) -> dict[str, Any] | None:
    """The recommended plan for the cohort the student names (a year in the message that a plan
    is published for), else for the current one, else the latest published; within a cohort,
    the plan named as the program itself rather than a variant of it."""
    cohorts = {str(p.get("cohort")) for p in plans}
    asked = [f"Fall {year}" for year in _YEAR.findall(message) if f"Fall {year}" in cohorts]
    current = int(current_cohort(today)[-4:])
    standing = next((years for word, years in _CLASS_YEARS if word.search(message.casefold())), 0)
    cohort = asked[0] if asked else f"Fall {current - standing}"
    if cohort not in cohorts:
        cohort = max(cohorts, key=lambda c: c[-4:], default="")
    pool = [p for p in plans if str(p.get("cohort")) == cohort]
    # A plan the message names by its own name (a concentration: "Contemporary Arts" of Visual
    # Arts) before the plan named as the program itself.
    words = discovery_tokens(message)
    core = program_core(program).casefold()
    asked_plan = [p for p in pool if (name := discovery_tokens(str(p.get("name") or "")))
                  and name <= words and str(p.get("name") or "").casefold() != core]
    named = [p for p in pool if str(p.get("name") or "").casefold() == core]
    chosen = asked_plan or named or pool
    return chosen[0] if chosen else None


def render_plan(plan: dict[str, Any]) -> str:
    """A recommended plan as published: who it applies to, each semester's items with their
    hours and the semester's published total, the published totals, Gen Ed categories (and
    which are waived for transfer students), and the placement note."""
    lines = [f"{plan.get('name')} — {plan.get('cohort')}"]
    if plan.get("applicability"):
        lines.append(str(plan["applicability"]))
    for term in plan.get("terms") or []:
        total = term.get("totalHours")
        lines.append(f"{term.get('year')}, {term.get('term')}"
                     + (f" (total {total:g} credits)" if isinstance(total, int | float) else ""))
        for item in term.get("items") or []:
            hours = item.get("hours")
            lines.append(f"  - {' '.join(str(item.get('text') or '').split())}"
                         + (f" ({hours:g})" if isinstance(hours, int | float) else ""))
    lines += [str(total) for total in plan.get("totals") or []]
    gen_ed = [g for g in plan.get("generalEducation") or [] if isinstance(g, dict)]
    if gen_ed:
        lines.append("General Education categories:")
        lines += [f"  - {g.get('category')}"
                  + (" (waived for transfer students)" if g.get("waivedForTransfers") else "")
                  for g in gen_ed]
    if plan.get("placementText"):
        lines.append(f"Placement: {' '.join(str(plan['placementText']).split())}")
    return "\n".join(lines)


def program_evidence(facts: dict[str, Any], n: int, others: Sequence[str],
                     message: str = "", today: date | None = None) -> dict[str, Any]:
    """What the reply is given for one program: who it is (from its catalog row), its
    requirements and its recommended graduation plan written out by code, and the program's
    other published levels."""
    props = {prop["key"]: prop for prop in facts["properties"]}
    program = props["program"]["values"][0]["value"]
    groups = [value["value"] for value in props.get("requirements", {}).get("values", [])]
    plans = [value["value"] for value in props.get("plans", {}).get("values", [])]
    plan = (choose_plan(plans, str(program.get("name") or ""), message, today)
            if plans and today else None)
    description = str(program.get("description") or "")
    return {"n": n, "program": program.get("name"), "degree": program.get("degree"),
            "school": program.get("school"), "catalog_url": program.get("catalog_url"),
            **({"description": description[:800]} if description else {}),
            "requirements": render_requirements(groups) if groups else None,
            **({"graduation_plan": render_plan(plan), "plan_url": plan.get("url")}
               if plan else {}),
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
    is exactly a published catalog code, or when its subject is a short form of exactly one
    catalog code's subject with the same number ("soc 301" is SOCI 301, "psych 101" PSYC 101),
    so "ASB 312" (a room) and "room 147" are not courses."""
    by_number: dict[str, list[str]] = {}
    for code in known:
        by_number.setdefault(code.rsplit(" ", 1)[-1], []).append(code)
    found: list[str] = []
    for subject, number in _TYPED_CODE.findall(message):
        typed, number = subject.upper(), number.upper()
        code = f"{typed} {number}"
        if code not in known and len(typed) >= 3:
            short = [c for c in by_number.get(number, [])
                     if (catalog := c.rsplit(" ", 1)[0]).startswith(typed)
                     or typed.startswith(catalog)]
            code = short[0] if len(short) == 1 else code
        if code in known and code not in found:
            found.append(code)
    return found[:MAX_COURSES]


_CONDITIONS = {"completedAllOf": "complete all of", "allOf": "all of",
               "completedAnyOf": "complete one of", "anyOf": "any of",
               "enrolledIn": "be enrolled in", "minimumGrade": "complete"}


def _requisite_rule(rule: dict[str, Any], depth: int) -> list[str]:
    """One published requisite rule and its sub-rules as lines: its name and condition, a
    minimum grade, its own text (a placement test's scores), its courses joined by each item's
    and/or, its note, then each sub-rule indented under it."""
    pad = "  " * depth
    condition = str(rule.get("condition") or "")
    how = _CONDITIONS.get(condition, "")
    if condition == "completedAtLeastXOf" and isinstance(rule.get("count"), int):
        how = f"complete at least {rule['count']} of"
    head = " ".join(part for part in (str(rule.get("name") or "").strip(), how) if part)
    lines = [f"{pad}{head}:"] if head else []
    constraints = rule.get("constraints") if isinstance(rule.get("constraints"), dict) else {}
    grade = (constraints or {}).get("grade") or rule.get("minimumGrade")
    if grade:
        lines.append(f"{pad}  (minimum grade: {grade})")
    if rule.get("text"):
        lines.append(f"{pad}  {' '.join(str(rule['text']).split())}")
    for item in rule.get("items") or []:
        codes = [c for c in item.get("codes") or [] if isinstance(c, dict)]
        if codes:
            joiner = " or " if item.get("logic") == "or" and len(codes) > 1 else " and "
            lines.append(f"{pad}  - {joiner.join(_course(c) for c in codes)}")
    if rule.get("note"):
        lines += [f"{pad}  {line.strip()}" for line in str(rule["note"]).splitlines()
                  if line.strip()]
    for sub in rule.get("subRules") or rule.get("sub_rules") or []:
        if isinstance(sub, dict):
            lines += _requisite_rule(sub, depth + 1)
    return lines


def requisite_codes(requisites: Sequence[dict[str, Any]]) -> set[str]:
    """Every course code a course's published requisites name, in any rule or sub-rule."""
    found: set[str] = set()

    def walk(rule: dict[str, Any]) -> None:
        for item in rule.get("items") or []:
            found.update(str(c.get("code")) for c in item.get("codes") or []
                         if isinstance(c, dict) and c.get("code"))
        for sub in rule.get("subRules") or rule.get("sub_rules") or []:
            if isinstance(sub, dict):
                walk(sub)
    for section in requisites:
        if isinstance(section, dict) and isinstance(section.get("rule"), dict):
            walk(section["rule"])
    return found


def render_requisites(requisites: Sequence[dict[str, Any]]) -> str:
    """A course's published prerequisites and corequisites as lines: each section, then its rule
    tree as published (sub-rules, conditions, minimum grades, placement tests, notes)."""
    lines: list[str] = []
    for section in requisites:
        if not isinstance(section, dict):
            continue
        lines.append(str(section.get("section") or "Requisite"))
        found = section.get("rule")
        if isinstance(found, dict):
            lines += _requisite_rule(found, 1)
    return "\n".join(lines)


MAX_REQUIRED_BY = 20


def course_evidence(facts: dict[str, Any], n: int,
                    required_by: Sequence[str] = ()) -> dict[str, Any]:
    """What the reply is given for one course, from its catalog record, and the catalog courses
    whose published prerequisites name it (from the meaning index's reverse index)."""
    course = facts["properties"][0]["values"][0]["value"]
    requisites = course.get("requisites") or []
    later = list(required_by)[:MAX_REQUIRED_BY]
    return {"n": n, "course": f"{course.get('code')} {course.get('name') or ''}".strip(),
            "credits": course.get("credits"), "school": course.get("school"),
            "description": course.get("description") or None,
            "attributes": course.get("attributes") or [],
            "prerequisites": render_requisites(requisites) if requisites else "none published",
            **({"is_a_prerequisite_for": later,
                **({"more_courses_list_it": len(required_by) - len(later)}
                   if len(required_by) > len(later) else {})} if later else {}),
            "catalog_url": course.get("catalog_url")}


_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}
_TITLE_FILLER = {"of", "and", "to", "the", "in", "for", "a", "an", "with", "on", "&", "lecture",
                 "lab", "laboratory"}
# Question words never read as the start of a title word ("for" is not Forensic).
_ASKING = {"for", "the", "and", "can", "how", "what", "when", "who", "why", "does", "did",
           "take", "taking", "need", "before", "after", "with", "without", "about", "class",
           "classes", "course", "courses", "credits", "credit", "prereqs", "prereq", "prerequisite",
           "prerequisites", "major", "minor", "year", "semester", "first", "next", "this", "that",
           "there", "have", "has", "will", "should", "count", "counts", "enough", "into", "from",
           "gen", "general", "education", "ed"}


def _title_words(title: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", title.casefold())
    return [_ROMAN.get(w, w) for w in words if w not in _TITLE_FILLER]


def _abbreviates(word: str, title_word: str) -> bool:
    """A question's word for a title's word: the word itself, its start ("calc" for calculus,
    "gen" for general), or its start once a trailing s or o is dropped ("stats", "orgo")."""
    if word == title_word:
        return True
    if word.isdigit() or title_word.isdigit() or len(word) < 3:
        return False
    return title_word.startswith(word) or (
        len(word) >= 4 and word[-1] in "so" and title_word.startswith(word[:-1]))


def courses_by_title(titles: dict[str, str], message: str) -> list[str]:
    """The courses a question names by an everyday form of their catalog title ("calc 2" is
    Calculus II, "gen chem 2" General Chemistry II, "orgo 1" Organic Chemistry I): every title
    word matched, or two of three with the title's number, at least one being a word. The
    best-covered titles, at most three; a question naming none gives none."""
    # A lone "i" is the speaker, not Roman one; "gen ed" is general education, so "gen" names a
    # title word only beside another one ("gen chem").
    tokens = re.findall(r"[a-z0-9]+", message.casefold())
    words = [w if w == "i" else _ROMAN.get(w, w) for w in tokens]
    scored: list[tuple[float, str]] = []
    for entity_id, title in titles.items():
        parts = _title_words(title)
        # A one-word title ("Internship", "Seminar") is too common a word to be named by.
        if len(parts) < 2:
            continue
        at = {p: [k for k, w in enumerate(words) if _abbreviates(w, p) and (
            w not in _ASKING or w == p or (w == "gen" and k + 1 < len(words)
                                           and words[k + 1] not in ("ed", "eds")))]
              for p in parts}
        hit = [p for p in parts if at[p]]
        if not any(not p.isdigit() for p in hit):
            continue
        # The words used sit together in the question, not spread across it.
        places = [min(at[p]) for p in hit]
        if max(places) - min(places) > len(parts) + 2:
            continue
        numbers = [p for p in parts if p.isdigit()]
        if numbers and not all(n in words for n in numbers):
            continue
        share = len(hit) / len(parts)
        if share == 1 or (share >= 2 / 3 and numbers):
            scored.append((share, entity_id))
    if not scored:
        return []
    best = max(score for score, _ in scored)
    return [entity_id for score, entity_id in scored if score == best][:3]

