"""Deterministic safety classification.

Runs before any model call (DESIGN.md §4-5). Two independent triggers:

- `suicidal_intent`: first-person expressed intent to end one's life. Every
  pattern encodes an explicit first-person construction directly (a literal
  "i "/"i'm"/"i am" subject, or a reflexive/possessive "myself"/"my life"
  that is grammatically first-person-only in standard English) rather than
  relying solely on nearby-pronoun heuristics — "wants to die", "no reason
  to live", and "better off dead" are anchored to "i want to die" / "i (have)
  no reason to live" / "i'd be better off dead" so third-person narration
  ("she wants to die") cannot match at all. A negation check in the local
  clause is kept as defense in depth for most patterns, except for the one
  phrase where the negation *is* the crisis statement ("I don't want to be
  alive anymore"). Every pattern — guarded and unguarded — also carries a
  reported/quoted-speech guard: a reporting verb ("said", "texted", ...) or
  an unambiguous opening double/curly quote earlier in the *same clause*
  suppresses it ('The note said "I want to die"'), so only a direct user
  statement triggers. That guard intentionally never treats a bare
  apostrophe as a quote mark — "I'm scared. I don't want to be alive
  anymore" must still trigger, and contractions are apostrophes, not quotes.
- `active_emergency`: unconsciousness, a current fire, or weapon use, stated
  as an active/present situation. Historical framing ("yesterday"),
  informational/procedural phrasing ("drill", "protocol"), and an explicit
  urgency override ("right now", "emergency") are all evaluated in the
  *local clause* around each individual trigger match, not the whole
  message — and urgency is checked first and wins outright within that
  clause, so "Yesterday this was a drill, but right now someone is
  shooting" (one clause, both markers present) still classifies as
  `active_emergency`. Benign possession ("has a gun" with no threat verb)
  and minor self-injury ("I stabbed my toe") are excluded by requiring an
  explicit threat/harm construction rather than a bare trigger word.

This is intentionally conservative toward false positives where recall for
genuine emergencies would otherwise suffer; see THREAT_MODEL.md §4. It is a
heuristic safety net, not a clinical or legal determination.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SafetyReason = Literal["active_emergency", "suicidal_intent"]

# Sentence-ish boundaries used to isolate the local clause around a match,
# for both the suicidal-intent reporting/negation guards and the
# active-emergency historical/informational/urgency guards below.
_CLAUSE_BOUNDARY_PATTERN = re.compile(r"[.?!;\n]")


@dataclass(frozen=True, slots=True)
class SafetyClassification:
    reason: SafetyReason


def _clause_prefix(message: str, index: int) -> str:
    """Text from the start of the clause containing `index` up to `index`."""
    left_boundary = 0
    for boundary in _CLAUSE_BOUNDARY_PATTERN.finditer(message, 0, index):
        left_boundary = boundary.end()
    return message[left_boundary:index]


def _clause_around(message: str, start: int, end: int) -> str:
    left_boundary = 0
    for boundary in _CLAUSE_BOUNDARY_PATTERN.finditer(message, 0, start):
        left_boundary = boundary.end()
    right_match = _CLAUSE_BOUNDARY_PATTERN.search(message, end)
    right_boundary = right_match.start() if right_match else len(message)
    return message[left_boundary:right_boundary]


# ---------------------------------------------------------------------------
# Suicidal intent
# ---------------------------------------------------------------------------

_NEGATION_PATTERN = re.compile(
    r"\b(don'?t|do\s+not|doesn'?t|didn'?t|never|no\s+longer|won'?t|wouldn'?t|"
    r"isn'?t|not\s+going\s+to|not\s+planning\s+to|not\s+thinking\s+about|"
    r"not\s+actually)\b",
    re.IGNORECASE,
)

_THIRD_PERSON_SUBJECT_PATTERN = re.compile(
    r"\b(he|she|they|him|her|them|his|hers|their|someone|somebody|"
    r"my\s+friend|a\s+friend|my\s+roommate|my\s+classmate|my\s+brother|"
    r"my\s+sister|my\s+partner)\b",
    re.IGNORECASE,
)

_REPORTING_VERB_PATTERN = re.compile(
    r"\b(said|says|told|texted|wrote|posted|messaged)\b", re.IGNORECASE
)

# Only unambiguous *opening* double/curly quotation marks count as a quote.
# A bare ASCII or curly apostrophe is never treated as one, because it is
# indistinguishable from a contraction ("I'm", "don't", "wasn't").
_QUOTE_OPEN_PATTERN = re.compile(r"[\"“]")


def _reported_or_third_person(prefix: str) -> bool:
    return bool(
        _THIRD_PERSON_SUBJECT_PATTERN.search(prefix)
        or _REPORTING_VERB_PATTERN.search(prefix)
        or _QUOTE_OPEN_PATTERN.search(prefix)
    )


# Every pattern below encodes an explicit first-person construction: a
# literal "i"/"i'm"/"i am" subject, or "myself"/"my (own) life", which are
# grammatically first-person-only in standard English (a third-person
# subject would require "himself"/"herself"/"his life" instead).
_SUICIDAL_GUARDED_PATTERNS = [
    re.compile(r"\b(kill|hurt|harm)\s+myself\b", re.IGNORECASE),
    re.compile(r"\bend(?:ing)?\s+my\s+(?:own\s+)?life\b", re.IGNORECASE),
    re.compile(
        r"\bi\s+(?:really\s+|just\s+|honestly\s+|still\s+)?want\s+to\s+die\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bwish(?:ed)?\s+i\s+(?:was|were)\s+dead\b", re.IGNORECASE),
    re.compile(
        r"\bi(?:'m|\s+am)\s+going\s+to\s+(?:kill\s+myself|end\s+it\s+all)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bmy\s+suicide\s+plan\b", re.IGNORECASE),
    re.compile(r"\b(?:i'?m|i\s+am)\s+suicidal\b", re.IGNORECASE),
    re.compile(
        r"\bi\s+(?:have\s+)?no\s+reason\s+to\s+live\b"
        r"|\bno\s+reason\s+for\s+me\s+to\s+live\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi(?:'d|\s+would|\s+am|'m)\s+(?:be\s+)?better\s+off\s+dead\b",
        re.IGNORECASE,
    ),
]

# Unguarded by negation: the negation *is* the crisis statement here. Still
# subject to the reported/quoted-speech guard, so only a direct user
# statement triggers.
_SUICIDAL_UNGUARDED_PATTERNS = [
    re.compile(
        r"\bi\s+don'?t\s+want\s+to\s+(?:be\s+alive|live)\s+anymore\b", re.IGNORECASE
    ),
]


def _suicidal_match(message: str) -> SafetyClassification | None:
    for pattern in _SUICIDAL_UNGUARDED_PATTERNS:
        match = pattern.search(message)
        if match and not _reported_or_third_person(_clause_prefix(message, match.start())):
            return SafetyClassification(reason="suicidal_intent")

    for pattern in _SUICIDAL_GUARDED_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        prefix = _clause_prefix(message, match.start())
        if _NEGATION_PATTERN.search(prefix):
            continue
        if _reported_or_third_person(prefix):
            continue
        return SafetyClassification(reason="suicidal_intent")

    return None


# ---------------------------------------------------------------------------
# Active emergency
# ---------------------------------------------------------------------------

_UNCONSCIOUS_PATTERN = re.compile(
    r"\b(is|are|seems?|looks?)\s+unconscious\b"
    r"|\bunconscious\s+and\s+(?:not\s+)?(?:breathing|responding)\b"
    r"|\b(?:not|isn'?t|stopped)\s+breathing\b"
    r"|\bwon'?t\s+wake\s+up\b"
    r"|\b(?:just\s+)?passed\s+out\b"
    r"|\bunresponsive\b",
    re.IGNORECASE,
)

_FIRE_PATTERN = re.compile(
    r"\bthere(?:'s|s|\s+is)\s+a\s+fire\b"
    r"|\b(?:room|dorm|building|kitchen|apartment)\s+is\s+on\s+fire\b"
    r"|\bfire\s+is\s+(?:spreading|burning|growing)\b"
    r"|\bsmoke\s+is\s+(?:everywhere|filling)\b"
    r"|\bflames?\s+(?:are\s+)?(?:coming|everywhere|spreading)\b",
    re.IGNORECASE,
)

_WEAPON_PATTERN = re.compile(
    r"\bactive\s+shooter\b"
    r"|\bsomeone\s+is\s+shooting\b"
    r"|\bshots?\s+(?:fired|being\s+fired)\b"
    r"|\b(?:being|got|was)\s+shot\b"
    r"|\b(?:got|being|was)\s+stabbed\b"
    r"|\bstabbing\s+(?:someone|people|him|her|them)\b"
    r"|\bpointing\s+a\s+(?:gun|knife|weapon)\s+at\b"
    r"|\bthreatening\s+(?:me|us|someone|people)\s+with\s+a\s+(?:gun|knife|weapon)\b"
    r"|\bwaving\s+a\s+(?:gun|knife|weapon)\s+around\b",
    re.IGNORECASE,
)

_EMERGENCY_TRIGGER_PATTERNS = [_UNCONSCIOUS_PATTERN, _FIRE_PATTERN, _WEAPON_PATTERN]

_HISTORICAL_CONTEXT_PATTERN = re.compile(
    r"\b(yesterday|last\s+night|last\s+week|earlier\s+(?:today|this)|"
    r"a\s+(?:few|couple\s+of)\s+(?:days|weeks|hours)\s+ago|in\s+the\s+past|"
    r"used\s+to|when\s+i\s+was|a\s+while\s+ago|previously)\b",
    re.IGNORECASE,
)

_INFORMATIONAL_OVERRIDE_PATTERN = re.compile(
    r"\b(procedure|policy|protocol|drill|prevent|preparedness|report\s+a|"
    r"training|hypothetically|in\s+case\s+of|if\s+there\s+(?:is|was)|"
    r"what\s+should\s+i\s+do\s+if|how\s+do\s+i\s+report)\b",
    re.IGNORECASE,
)

_URGENCY_OVERRIDE_PATTERN = re.compile(
    r"\b(right\s+now|currently|immediately|urgent(?:ly)?|emergency|"
    r"call\s+911|please\s+help|someone\s+call|help\s+me)\b",
    re.IGNORECASE,
)


def _emergency_match(message: str) -> SafetyClassification | None:
    for pattern in _EMERGENCY_TRIGGER_PATTERNS:
        for match in pattern.finditer(message):
            clause = _clause_around(message, match.start(), match.end())
            # Urgency is checked first and wins outright within the clause:
            # a clause can legitimately contain both a historical/
            # informational marker *and* an urgent one ("this was a drill,
            # but right now someone is shooting"), and the urgent reading
            # must not be suppressed by the other marker sharing its clause.
            if _URGENCY_OVERRIDE_PATTERN.search(clause):
                return SafetyClassification(reason="active_emergency")
            if _HISTORICAL_CONTEXT_PATTERN.search(clause):
                continue
            if _INFORMATIONAL_OVERRIDE_PATTERN.search(clause):
                continue
            return SafetyClassification(reason="active_emergency")
    return None


def classify_safety(message: str) -> SafetyClassification | None:
    return _suicidal_match(message) or _emergency_match(message)
