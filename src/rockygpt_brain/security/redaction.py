"""Heuristic PII/secret redaction applied to *stored* chat text only.

Per spec/acceptance.md: "Stored questions, answers, and comments are
redacted for student IDs, email, phone, payment/SSN-like numbers, and
common secrets." This module is never applied to the live response shown
to the caller — only to the copy written by persistence/chat_logs.py. See
THREAT_MODEL.md §3.3 and §4: this is heuristic defense in depth, bounded by
the 30-day text-expiry backstop, not a guarantee of perfect redaction.

Patterns run most-specific first so that once a span is replaced with a
digit-free `[redacted-*]` marker, later, broader patterns (e.g. the generic
long-digit-run check) cannot re-match inside it.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_SECRET_PATTERNS = re.compile(
    r"sk-[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|Bearer\s+[A-Za-z0-9._-]{20,}"
)
_STUDENT_ID = re.compile(r"(?<!\d)(?:[A-Za-z]{1,2})?\d{7,9}(?!\d)")
# Payment-like runs of 13-19 digits, contiguous or grouped with spaces/
# dashes (e.g. "4111 1111 1111 1111", "4111-1111-1111-1111",
# "4111111111111111"). Every separator sits strictly *between* two digits
# (the trailing `\d` after the optional-separator group enforces that), so
# the match can never swallow a trailing word-boundary space/hyphen that
# isn't actually part of the number.
_PAYMENT_LIKE = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("email", _EMAIL),
    ("secret", _SECRET_PATTERNS),
    ("ssn", _SSN),
    ("phone", _PHONE),
    ("payment", _PAYMENT_LIKE),
    ("student-id", _STUDENT_ID),
]


def _marker(label: str) -> Callable[[re.Match[str]], str]:
    def _replace(_match: re.Match[str]) -> str:
        return f"[redacted-{label}]"

    return _replace


def redact(text: str | None) -> str | None:
    if text is None:
        return None
    redacted = text
    for label, pattern in _RULES:
        redacted = pattern.sub(_marker(label), redacted)
    return redacted
