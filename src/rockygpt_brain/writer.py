"""The writer: it says what the writer view holds, and a verifier refuses anything it adds.

The Brain's job ends at the validated Fact Packet and its writer view (writer_view.py). A writer
has no tools and no other context: it is handed that view and nothing else, so it cannot look
anything up, and what it may say is whatever the view holds. This module is that model call and the
check that its words stay inside the view.

The check is deliberately blunt and cheap. Every email address and web address in the reply must be
in the view, every phone number must be one the view holds (in any punctuation), and every other
number must be a number the view holds (a time, a date, a room). A reply that fails any of those
is thrown away and the code-written text is used instead, so a writer's slip never reaches a
student as a fact. What a number check cannot see (a wrong day, a reversed meaning) is audited
separately; the verifier is the floor, not the whole.
"""

import json
import re
from typing import Any

WRITE_TOOL: dict[str, Any] = {
    "type": "function", "name": "write_answer", "strict": True,
    "description": "Write the reply the student reads, from the facts you were given.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["text"],
                   "properties": {"text": {"type": "string", "maxLength": 1200}}},
}

WRITER_PROMPT = """You write the reply a Ramapo College student reads. You are given facts about
campus offices as JSON, and nothing else: you cannot look anything up and you know nothing beyond
the JSON.

Say only what the JSON says, in short plain sentences, as if to one student.
- Say every email address, phone number, room, time, date and web address exactly as it appears.
  Never compute, round, convert or guess a value. Never add a fact, a policy, advice or a reason
  that is not in the JSON.
- asked.fields is what the student asked for: answer that first. Other facts come after, briefly.
  A fact with purpose "also_published" is extra: say it last and briefly.
- derived_facts hold the hours of the one day the student asked about: say that day's hours, and
  not the whole week.
- A fact with current false, or a schedule with dates_not_published or applicability
  "unverified", is what the office's page lists but is not confirmed for the date: say it is
  listed that way and not confirmed.
- not_published means the office's own pages were read on the checked_at date and publish nothing
  about it: say they do not publish it, and when it was checked.
- missing means you do not have it: say you do not have verified information about that.
- ambiguities.choices: ask which of those offices they mean, naming each.
- Do not repeat notices: the server shows their fixed wording itself.
- When a statement comes from a source, put its number after it like [1].
- At most six sentences. No greeting, no sign-off, no headings."""

_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_URL = re.compile(r"https?://[^\s)\]>\"']+")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_NUMBER = re.compile(r"\d+")
_MARKER = re.compile(r"\[(\d+)\]")


def writer_messages(view: dict[str, Any]) -> list[dict[str, Any]]:
    """The whole of what the writer is given."""
    return [{"role": "system", "content": WRITER_PROMPT},
            {"role": "user", "content": json.dumps(view, ensure_ascii=False)}]


def unsupported_by(text: str, view: dict[str, Any]) -> list[str]:
    """What in the reply the view does not hold; empty means the reply may be sent."""
    held = json.dumps(view, ensure_ascii=False).casefold()
    digits = re.sub(r"\D", "", held)
    numbers = set(_NUMBER.findall(held))
    sources = {str(source["n"]) for source in view.get("sources", [])}
    rest = text.casefold()
    problems: list[str] = []
    for email in _EMAIL.findall(rest):
        if email not in held:
            problems.append(f"an email address the facts do not hold: {email}")
    rest = _EMAIL.sub(" ", rest)
    for url in _URL.findall(rest):
        if url.rstrip(".,;:") not in held:
            problems.append(f"a web address the facts do not hold: {url}")
    rest = _URL.sub(" ", rest)
    for phone in _PHONE.findall(rest):
        number = re.sub(r"\D", "", phone)[-10:]
        if number not in digits:
            problems.append(f"a phone number the facts do not hold: {phone.strip()}")
    rest = _PHONE.sub(" ", rest)
    rest = _MARKER.sub(lambda m: " " if m.group(1) in sources else m.group(0), rest)
    for number in _NUMBER.findall(rest):
        if number not in numbers:
            problems.append(f"a number the facts do not hold: {number}")
    return problems


def says_something(view: dict[str, Any]) -> bool:
    """Whether the view holds facts for a writer to say (not only a question or fixed wording)."""
    return any(view.get(key) for key in ("facts", "derived_facts", "not_published", "missing"))
