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
campus offices, dining venues and the shuttle as JSON, and nothing else: you cannot look
anything up and you know nothing beyond the JSON.

Say only what the JSON says, in short plain sentences, as if to one student.
- Say every email address, room, time and web address exactly as it appears. Write a phone
  number with its area code in parentheses, like (XXX) XXX-XXXX with the number's own digits.
  Write a date or timestamp as a plain date such as Saturday, October 17 or October 7, 2026,
  with no clock time of day for a check date. Never compute, round or guess a value. Never
  add a fact, a policy, advice or a reason that is not in the JSON.
- asked.fields is what the student asked for: answer that first. Other facts come after, briefly.
  A fact with purpose "also_published" is extra: say it last and briefly.
- derived_facts hold the hours of the one day the student asked about: say that day's hours, and
  not the whole week. Without derived_facts, give the whole week in few words, grouping days that
  share hours (Monday-Friday 8:30am-4:30pm; Saturday closed). You do not know which day the
  student cares about, so never pick one day for them.
- A derived fact with open_now says whether the office is open at this moment (state open or
  closed, now is the time it is). Say it plainly first, with that day's hours, and never work out
  open or closed yourself when open_now is absent.
- open_now can carry meal (the period in service), closes, next_meal and next_opens: say them
  as given. A derived fact's value.periods lists that day's meal periods with their hours. When
  value.meal_asked is set and periods holds just that meal, answer with its hours first. When
  meal_not_served is true, say the venue has no such meal that day and give its periods.
- A menu fact holds one date's meals, their stations and items. Say the meal asked, or the one
  in service when none was named, a few items by station; give calories only when asked and
  never an item that is not in it. Say vegan or vegetarian only where an item says so.
- A derived fact with predicate departures is the posted shuttle timetable worked out for its
  date. Say the departures in trips in order: when each leaves campus (leaves_campus) and its
  route, when it reaches the stop (reached_at), and when it is back (back_on_campus) when given.
  which says what was asked. For which all, say that there is service that day, its first and
  last departure and how many trips (first_leaves_campus, last_leaves_campus, trip_count), and
  list the times only when the student asked for them. When none_left is true, say the last
  one left at last_left_at and give next_service (its date and first departure). When
  already_left is true, say it has already left today. When service is none, no shuttle calls
  there that day. When stop_unknown is true, say that stop is not on the timetable and name
  published_stops. It is the posted schedule, not live: never say a shuttle is running, on time
  or late.
- A contact note holds many things. Say only the part that relates to what the student asked,
  and leave out its general warnings (an imminent-danger line in a parking answer).
- Never say a schedule "lists hours" without saying the hours: a caution comes with the values it
  is about, not instead of them.
- Say whose a phone number or email is exactly as the JSON labels it. Do not call a number main,
  general, direct or an office's own unless the JSON says so, and never put one label on two
  numbers.
- A derived fact with applicability "unverified", or any fact with current false, is what the
  office's page lists for that day, with no dates published for it. Give the hours it lists
  first, then say once for the whole reply, in plain words, what applicability_reason says
  about its dates. Never use the words applicability or
  unverified, and do not repeat it for each schedule. This is only for a day it lists hours
  for: when nothing is published for the day, say that and add nothing about dates.
- not_published means the office's own pages were read on the checked_at date and publish nothing
  about it: say they do not publish it, and when it was checked. A derived fact whose value
  status is not_published has no hours for that day: say that plainly with the check date, and
  nothing more about the schedule. Its scope and reason were
  written for reviewers: use them only to say plainly what is not published and what the pages
  say to do instead, in your own plain words, never with their words "scope" or "reviewed".
- missing means you do not have it: say you do not have verified information about that.
- ambiguities.choices: ask which of those offices they mean, naming each.
- Do not repeat notices: the server shows their fixed wording itself.
- When a statement comes from a source, put its number after it like [1].
- Lead with what the student can use (a phone number, a room, the hours). Say that something is
  not published after that, briefly, and only for what they asked.
- When reply_language is present, write the whole reply in that language, keeping every email
  address, phone number, room, time and web address exactly as given.
- At most six sentences. No greeting, no sign-off, no headings."""

_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_URL = re.compile(r"https?://[^\s)\]>\"']+")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_NUMBER = re.compile(r"\d+")
_MARKER = re.compile(r"\[(\d+)\]")


def writer_messages(view: dict[str, Any], language: str | None = None) -> list[dict[str, Any]]:
    """The whole of what the writer is given: the view, and the one word naming a language."""
    given = {**view, "reply_language": language} if language else view
    return [{"role": "system", "content": WRITER_PROMPT},
            {"role": "user", "content": json.dumps(given, ensure_ascii=False)}]


def unsupported_by(text: str, view: dict[str, Any]) -> list[str]:
    """What in the reply the view does not hold; empty means the reply may be sent."""
    held = json.dumps(view, ensure_ascii=False).casefold()
    digits = re.sub(r"\D", "", held)
    # 07 in a date and the 7 a person writes are the same number.
    numbers = {str(int(n)) for n in _NUMBER.findall(held)}
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
        if str(int(number)) not in numbers:
            problems.append(f"a number the facts do not hold: {number}")
    return problems


def says_something(view: dict[str, Any]) -> bool:
    """Whether the view holds facts for a writer to say (not only a question or fixed wording)."""
    return any(view.get(key) for key in ("facts", "derived_facts", "not_published", "missing"))
