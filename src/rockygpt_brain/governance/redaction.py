"""Student-written text is stored only after common personal details are removed.

Feedback carries the question a student rated and any comment they typed.
Either can hold something that should never be kept: an email address, a
personal phone number, a Ramapo student number, a Social Security or card
number. Campus phone numbers (201-684-xxxx) are public and stay, because a
report that "201-684-7695 is wrong" is only useful with the number in it.
"""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn]"),
    (re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), "[card]"),
    (re.compile(r"\b[Rr]\d{8}\b"), "[student id]"),
)
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?\d{4}(?!\d)")


def redact(text: str | None) -> str | None:
    """Remove personal details from text a student wrote; campus numbers stay."""
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return _PHONE.sub(
        lambda match: match.group(0)
        if (match.group(1), match.group(2)) == ("201", "684")
        else "[phone]",
        text,
    )
