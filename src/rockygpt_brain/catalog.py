"""Which catalog programs and courses a question means, picked by the model from the catalog's own
names, and the lookups code answers across the whole catalog for the picked ones.

Word rules ("comm" is Communication, "bio" is Biology) kept breaking one question while fixing
another (eight blind rounds at 60-80%, 10-27). The model now reads the catalog's 134 program names
and the courses near the question and picks what the student means; it can only pick names the
catalog has. Code then reads each pick's published record and answers the cross-catalog lookups
(which programs list a course, which courses require it) from the data itself."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

MAX_PICKED_PROGRAMS, MAX_PICKED_COURSES = 4, 6
MAX_CANDIDATES = 15  # Courses offered to the pick per part of the question, by meaning.
TRACKS = " | plan tracks: "  # How a program's tracks follow its name in the pick's list.
MAX_LISTING = 30  # Programs named in one "programs listing this course" fact.
LEVELS = ("major", "minor", "certificate", "graduate", "special")
FINDS = ("programs_listing_course", "programs_at_level")

PICK_PROMPT = (
    "A Ramapo College student asked a question about academics. Below are the college catalog's "
    "programs (every one) and courses that may be relevant, by their catalog names. Pick what the "
    "student means, using only names from these lists:\n"
    "- programs: the programs the question is about, as the student would mean them (\"comm "
    "major\" is Communication Arts BA; \"bio\" is Biology BS; \"criminal justice\" may be a track "
    "of another program: its plan tracks are listed after it, so pick that program). At most 4. "
    "A program the catalog does not have is not picked; pick the nearest real ones only when "
    "the student would want them.\n"
    "- courses: the courses the question names or clearly means (\"orgo 1\" is CHEM 211, \"calc "
    "2\" is MATH 122), by code. At most 6.\n"
    "- find: programs_listing_course when the question asks which programs or majors require or "
    "count a picked course; programs_at_level when it asks which programs exist at a level "
    "(\"what minors are there\", \"which majors have a 4+1\").\n"
    "- level: the level asked about (major, minor, certificate, graduate, special for 4+1), or "
    "null.\n"
    "- track: the track or concentration the student names in their own words (\"pre-med\", "
    "\"physical therapy\"), or null.\n"
    "Answer only with the pick_catalog tool.")

PICK_TOOL: dict[str, Any] = {
    "type": "function", "name": "pick_catalog", "strict": True,
    "description": "The catalog programs and courses the question means, and the lookups it needs.",
    "parameters": {
        "type": "object", "additionalProperties": False,
        "required": ["programs", "courses", "find", "level", "track"],
        "properties": {
            "programs": {"type": "array", "items": {"type": "string"}},
            "courses": {"type": "array", "items": {"type": "string"}},
            "find": {"type": "array", "items": {"type": "string", "enum": list(FINDS)}},
            "level": {"type": ["string", "null"], "enum": [*LEVELS, None]},
            "track": {"type": ["string", "null"]},
        },
    },
}


def pick_messages(question: str, programs: Sequence[str],
                  courses: Sequence[tuple[str, str]],
                  tracks: Mapping[str, Sequence[str]] | None = None) -> list[dict[str, Any]]:
    """The question, every program name (with its recommended plans' tracks), and the
    candidate courses as "CODE Title"."""
    lines = ["Programs:", *(f"{name}{TRACKS}{'; '.join(tracks[name])}"
                            if tracks and tracks.get(name) else name for name in programs),
             "", "Courses:",
             *(f"{code} {title}".strip() for code, title in courses)]
    return [{"role": "system", "content": PICK_PROMPT},
            {"role": "user", "content": f"Question: {question}\n\n" + "\n".join(lines)}]


def read_pick(arguments: Mapping[str, Any] | None, programs: Mapping[str, dict[str, Any]],
              courses: Mapping[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The pick kept to names the catalog has (programs by exact name, courses by code, any
    case); None when the tool's answer is unusable."""
    if not isinstance(arguments, Mapping):
        return None
    by_name = {name.casefold(): value for name, value in programs.items()}
    names = [str(name).split(TRACKS)[0] for name in arguments.get("programs") or []]
    by_code = {" ".join(code.split()).casefold(): value for code, value in courses.items()}
    picked_programs = list({id(p): p for p in (
        by_name.get(name.strip().casefold()) for name in names)
        if p is not None}.values())[:MAX_PICKED_PROGRAMS]
    codes = [f"{m.group(1)} {m.group(2)}".casefold() for code in arguments.get("courses") or []
             if (m := _PICKED_CODE.match(str(code).strip()))]
    picked_courses = list({id(c): c for c in (by_code.get(code) for code in codes)
                           if c is not None}.values())[:MAX_PICKED_COURSES]
    level = arguments.get("level")
    track = arguments.get("track")
    return {"programs": picked_programs, "courses": picked_courses,
            "find": [f for f in arguments.get("find") or [] if f in FINDS],
            "level": level if level in LEVELS else None,
            "track": str(track).strip() if isinstance(track, str) and track.strip() else None}


_CODE = re.compile(r"\b([A-Z]{2,6} \d{1,3}[A-Z]?)\b")
# A picked course as the model may write it: its code, alone or before its title.
_PICKED_CODE = re.compile(r"([A-Za-z]{2,6})\s*-?\s*(\d{1,3}[A-Za-z]?)\b")


def courses_listed(requirements: str) -> set[str]:
    """The course codes a program's requirements, as code writes them, list."""
    return set(_CODE.findall(requirements))


def listing_facts(codes: Sequence[str], listed_in: Mapping[str, Sequence[str]],
                  n: int | None) -> list[dict[str, Any]]:
    """For each course, the catalog programs whose requirements list it, or that none does."""
    facts = []
    for code in codes:
        names = sorted(listed_in.get(code) or [])
        if not names:
            facts.append({"fact": f"No program's requirements in the catalog list {code}.",
                          "n": n})
            continue
        shown = names[:MAX_LISTING]
        facts.append({"fact": f"The catalog lists {code} in the requirements of {len(names)} "
                              f"program{'s' if len(names) != 1 else ''}: {'; '.join(shown)}"
                              + (f" (and {len(names) - len(shown)} more)."
                                 if len(names) > len(shown) else "."), "n": n})
    return facts


def order_facts(courses: Sequence[dict[str, Any]],
                requisites: Mapping[str, set[str]]) -> list[dict[str, Any]]:
    """For each pair of courses given, whether one's published prerequisites name the other, so
    "which comes first" is said from the catalog."""
    facts = []
    for later in courses:
        for earlier in courses:
            if earlier is later:
                continue
            a, b = str(earlier["code"]), str(later["code"])
            if a in requisites.get(b, set()):
                facts.append({"fact": f"{b}'s published prerequisites name {a}, so {a} comes "
                                      f"before {b} (or with it, where the catalog says it may "
                                      "be taken concurrently).", "n": later.get("n")})
    return facts
