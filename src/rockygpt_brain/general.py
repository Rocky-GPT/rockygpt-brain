"""A short general answer to what is not about Ramapo (Dan, 11-18 decision: yes).

Students text mixed messages ("is coffee bad for u lol. also how do i switch my major") and plain
everyday questions ("how do i make friends", "why is the sky blue", "tips for finals"). Until
11-18 every one of them got "I only know about Ramapo College" (the 11-18 blind set: 7 of 17
non-campus questions refused, and two more answered with a support line instead).

The read names the part that is not about Ramapo (triage.Reading.general). One small model call
answers that part from general knowledge, briefly. Its reply holds no campus fact: campus facts
stay graph-only, so a reply naming Ramapo, a web address, an email or a phone number is not
used. Code marks it as general, so a student never takes it for the college's word. Where a
Ramapo office, club or service fits, the read also asks that campus side as the campus question,
which the usual answer paths answer from the graph after it.

Not answered here: doing graded homework or writing (those limits stay), and a request for
medical advice (a medicine, a dose, a symptom's meaning), which keeps its own fixed line.
"""

import re
from typing import Any

GENERAL_PROMPT = """You are RockyGPT, a friendly assistant for college students. The student asked \
something that is not about their college. Answer it from general knowledge, briefly and \
plainly, the way a smart friend would.
- At most 4 short sentences, or up to 5 short tips as a list. No greeting, no sign-off.
- Never name a college, office, club, event, phone number, email or web address: what the \
college offers is added after your answer by someone else.
- Food safety, health and fitness: give only widely accepted general guidance (such as safe \
cooking temperatures from food-safety agencies), and say to check the package, a doctor or a \
pharmacist when it matters. Never give a medicine, a dose or a diagnosis.
- A word or grammar question: explain the rule with a short example; never write the student's \
assignment for them.
- A playful or opinion question (who would win, which hero is stronger): answer playfully in one \
or two sentences and say it is just for fun.
- If you are not sure of a fact, say so instead of guessing.
Answer only with the write_general tool."""

GENERAL_TOOL: dict[str, Any] = {
    "type": "function", "name": "write_general", "strict": True,
    "description": "The short general answer.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["text"],
                   "properties": {"text": {"type": "string", "maxLength": 1200}}},
}

# How code marks the general answer, so it is never read as the college's own word.
LABEL = "**General answer** (from general knowledge, not Ramapo's pages):"
MAX_CHARS = 900

# What a general answer may not hold: a campus fact or a way to reach anyone (graph-only rule).
_NOT_GENERAL = re.compile(
    r"ramapo|https?://|www\.|\b[\w.+-]+@[\w-]+\.\w+|\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b",
    re.IGNORECASE)


def messages(asked: str) -> list[dict[str, Any]]:
    """The general writer's whole input: its fixed prompt, then only what was asked."""
    return [{"role": "system", "content": GENERAL_PROMPT},
            {"role": "user", "content": asked}]


def checked(text: Any) -> str | None:
    """The general answer as it may be said, or None: empty, too long, or holding a campus name,
    a link, an email or a phone number."""
    if not isinstance(text, str):
        return None
    said: str = text.strip()
    if not said or len(said) > MAX_CHARS or _NOT_GENERAL.search(said):
        return None
    return said


def said(text: str) -> str:
    """The general answer as code says it: marked as general."""
    return f"{LABEL} {text}" if "\n" not in text else f"{LABEL}\n{text}"
