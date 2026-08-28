from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ProgramKind = Literal["major", "minor", "certificate", "special", "undeclared"]
ProgramDegreeLevel = Literal["undergraduate", "graduate", "masters", "doctoral", "phd"]


@dataclass
class ProgramSearchCriteria:
    subject: str
    subject_tokens: list[str]
    requested_kind: ProgramKind | None = None
    requested_degree: str | None = None
    requested_level: ProgramDegreeLevel | None = None


QUESTION_AND_PROGRAM_WORDS = {
    "a",
    "about",
    "an",
    "and",
    "any",
    "are",
    "at",
    "available",
    "can",
    "certificate",
    "certificates",
    "college",
    "degree",
    "degrees",
    "do",
    "does",
    "for",
    "have",
    "i",
    "in",
    "is",
    "list",
    "major",
    "majors",
    "me",
    "minor",
    "minors",
    "of",
    "offer",
    "offered",
    "program",
    "programs",
    "ramapo",
    "school",
    "show",
    "tell",
    "the",
    "there",
    "to",
    "what",
    "which",
    "with",
}

PROGRAM_ALIASES: dict[str, list[str]] = {
    "bio": ["biology"],
    "cs": ["computer", "science"],
}

DEGREE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:doctor of philosophy|ph\.?\s*d\.?)\b", re.I), "Doctor of Philosophy"),
    (
        re.compile(r"\b(?:bachelor of science in nursing|bsn)\b", re.I),
        "Bachelor of Science in Nursing",
    ),
    (re.compile(r"\b(?:master of science in nursing|msn)\b", re.I), "Master of Science in Nursing"),
    (re.compile(r"\b(?:doctor of nursing practice|dnp)\b", re.I), "Doctor of Nursing Practice"),
    (
        re.compile(r"\b(?:master of business administration|mba)\b", re.I),
        "Master of Business Administration",
    ),
    (re.compile(r"\b(?:master of public policy|mpp)\b", re.I), "Master of Public Policy"),
    (re.compile(r"\b(?:master of social work|msw)\b", re.I), "Master of Social Work"),
    (re.compile(r"\b(?:bachelor of social work|bsw)\b", re.I), "Bachelor of Social Work"),
    (re.compile(r"\b(?:master of fine arts|mfa)\b", re.I), "Master of Fine Arts"),
    (re.compile(r"\b(?:graduate certificate)\b", re.I), "Graduate Certificate"),
    (re.compile(r"\b(?:bachelor of science|bs)\b", re.I), "Bachelor of Science"),
    (re.compile(r"\b(?:bachelor of arts|ba)\b", re.I), "Bachelor of Arts"),
    (re.compile(r"\b(?:master of science|ms)\b", re.I), "Master of Science"),
    (re.compile(r"\b(?:master of arts|ma)\b", re.I), "Master of Arts"),
]

DEGREE_LEVEL_PATTERNS: list[tuple[re.Pattern[str], ProgramDegreeLevel]] = [
    (re.compile(r"\b(?:doctor of philosophy|ph\.?\s*d\.?)\b", re.I), "phd"),
    (re.compile(r"\b(?:doctoral|doctorate)\b", re.I), "doctoral"),
    (re.compile(r"\b(?:master(?:['’]s|s)?|master-level)\b", re.I), "masters"),
    (re.compile(r"\bgraduate\b", re.I), "graduate"),
    (re.compile(r"\bundergraduate\b", re.I), "undergraduate"),
]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+\s]", " ", value.lower())).strip()


def parse_program_search(query: str) -> ProgramSearchCriteria:
    subject_text = re.sub(
        r"\b(?:4\s*\+\s*1|five[ -]year|combined degree)\b", " ", query, flags=re.I
    )
    for pattern, _ in DEGREE_PATTERNS:
        subject_text = pattern.sub(" ", subject_text)
    for pattern, _ in DEGREE_LEVEL_PATTERNS:
        subject_text = pattern.sub(" ", subject_text)

    tokens: list[str] = []
    for token in normalize_text(subject_text).split():
        if not token:
            continue
        if token in PROGRAM_ALIASES:
            tokens.extend(PROGRAM_ALIASES[token])
        else:
            tokens.append(token)

    filtered_tokens = [t for t in tokens if len(t) >= 3 and t not in QUESTION_AND_PROGRAM_WORDS]
    if len(filtered_tokens) == 1 and filtered_tokens[0] == "art":
        filtered_tokens = ["visual", "arts"]

    subject_tokens = list(dict.fromkeys(filtered_tokens))

    # Kind
    req_kind: ProgramKind | None = None
    if re.search(r"\b(?:minor|minor program)s?\b", query, re.I):
        req_kind = "minor"
    elif re.search(r"\b(?:certificate|certificate program)s?\b", query, re.I):
        req_kind = "certificate"
    elif re.search(r"\b(?:4\s*\+\s*1|five[ -]year|combined degree)\b", query, re.I):
        req_kind = "special"
    elif re.search(r"\b(?:major|major program)s?\b", query, re.I):
        req_kind = "major"

    # Degree
    req_degree: str | None = None
    for pattern, deg in DEGREE_PATTERNS:
        if pattern.search(query):
            req_degree = deg
            break

    # Level
    req_level: ProgramDegreeLevel | None = None
    for pattern, lvl in DEGREE_LEVEL_PATTERNS:
        if pattern.search(query):
            req_level = lvl
            break
    if req_level is None and req_degree:
        if req_degree == "Doctor of Philosophy":
            req_level = "phd"
        elif req_degree.startswith("Doctor of "):
            req_level = "doctoral"
        elif req_degree.startswith("Master of "):
            req_level = "masters"
        elif req_degree == "Graduate Certificate":
            req_level = "graduate"

    return ProgramSearchCriteria(
        subject=" ".join(subject_tokens),
        subject_tokens=subject_tokens,
        requested_kind=req_kind,
        requested_degree=req_degree,
        requested_level=req_level,
    )


def infer_program_kind(name: str, degree: str | None, program_kind: str | None) -> ProgramKind:
    if program_kind in {"major", "minor", "certificate", "special", "undeclared"}:
        return program_kind  # type: ignore[return-value]
    if re.search(r"\b4\s*\+\s*1\b", name, re.I):
        return "special"
    label = f"{degree or ''} {name}"
    if re.search(r"certificate", label, re.I):
        return "certificate"
    if re.search(r"\bminor\b", label, re.I):
        return "minor"
    if re.search(r"undeclared|non-degree", name, re.I):
        return "undeclared"
    return "major"


def program_matches_criteria(
    name: str,
    degree: str | None,
    program_kind: str | None,
    criteria: ProgramSearchCriteria,
) -> bool:
    if (
        criteria.requested_kind
        and infer_program_kind(name, degree, program_kind) != criteria.requested_kind
    ):
        return False
    if criteria.requested_degree and not program_matches_requested_degree(
        name, degree, criteria.requested_degree
    ):
        return False
    if criteria.requested_level and not program_matches_degree_level(
        name, degree, criteria.requested_level
    ):
        return False
    return True


def program_matches_requested_degree(name: str, degree: str | None, requested: str) -> bool:
    if normalize_text(degree or "") == normalize_text(requested):
        return True
    norm_req = normalize_text(requested)
    distinctive_aliases: dict[str, re.Pattern[str]] = {
        "bachelor of science in nursing": re.compile(r"\bbsn\b", re.I),
        "bachelor of social work": re.compile(r"\bbsw\b", re.I),
        "doctor of nursing practice": re.compile(r"\bdnp\b", re.I),
        "doctor of philosophy": re.compile(r"\bph\.?\s*d\.?\b", re.I),
        "master of business administration": re.compile(r"\bmba\b", re.I),
        "master of fine arts": re.compile(r"\bmfa\b", re.I),
        "master of public policy": re.compile(r"\bmpp\b", re.I),
        "master of science in nursing": re.compile(r"\bmsn\b", re.I),
        "master of social work": re.compile(r"\bmsw\b", re.I),
        "graduate certificate": re.compile(r"\bgraduate certificate\b", re.I),
    }
    pat = distinctive_aliases.get(norm_req)
    return bool(pat.search(name)) if pat else False


def program_matches_degree_level(
    name: str, degree: str | None, requested: ProgramDegreeLevel
) -> bool:
    norm_degree = normalize_text(degree or "")
    norm_name = normalize_text(name)
    combined = f"{norm_degree} {norm_name}"

    is_phd = bool(
        re.search(r"\b(?:doctor of philosophy|phd|ph d)\b", norm_degree)
        or re.search(r"\b(?:phd|ph d)\b", norm_name)
    )
    is_doctoral = bool(
        is_phd
        or re.search(r"\b(?:doctor(?: of)?|doctoral)\b", norm_degree)
        or re.search(r"\bdnp\b", norm_name)
    )
    is_masters = bool(
        re.search(r"\bmaster(?: of)?\b", norm_degree)
        or re.search(r"\b(?:mba|mfa|mpp|msn|msw)\b", norm_name)
    )
    is_grad_cert = bool(re.search(r"\bgraduate certificate\b", combined))
    is_graduate = (
        is_doctoral
        or is_masters
        or is_grad_cert
        or bool(re.search(r"\bgraduate program\b", norm_degree))
    )
    is_undergrad = not is_graduate and bool(
        re.search(r"\b(?:bachelor|undergraduate|minor|ba|bs|bsn|bsw)\b", combined)
    )

    if requested == "phd":
        return is_phd
    if requested == "doctoral":
        return is_doctoral
    if requested == "masters":
        return is_masters
    if requested == "graduate":
        return is_graduate
    return is_undergrad
