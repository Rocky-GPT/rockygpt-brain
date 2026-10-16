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

# The reply's longest length. Strict output stops a longer reply at exactly this many characters,
# mid-word, so a reply that reaches it was cut short and is not used (the code's text is).
WRITER_TEXT_LIMIT = 2400
WRITE_TOOL: dict[str, Any] = {
    "type": "function", "name": "write_answer", "strict": True,
    "description": "Write the reply the student reads, from the facts you were given.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["text"],
                   "properties": {"text": {"type": "string", "maxLength": WRITER_TEXT_LIMIT}}},
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
- open_now can carry meal (the period in service), closes, next_meal and next_opens: say them as
  given. closes is when service stops; meal_ends and then_meal mean the next meal follows straight
  on, so never say it closes at meal_ends. A derived fact's value.periods lists that day's meal
  periods with their hours. When value.meal_asked is set and periods holds just that meal, answer
  with its hours first. When meal_not_served is true, say the venue has no such meal that day and
  give its periods. When meals_not_labelled is true the venue publishes one set of hours with no
  meal names: give the hours and never say a meal is or is not served. closed_gaps lists when the
  venue is closed between its periods (empty: open straight through); use it for "does it close
  between".
- A menu fact holds one date's meals, their stations and items. Say each meal it holds (a menu is
  never narrowed to the meal in service), naming every station with a few of its items, and never an
  item that is not in it. Say vegan or vegetarian only where an item says so. When meal_not_listed
  is set, that date's menu has no such meal: say so and name meals_listed. When filter.contains is
  set the student asked about those foods: give each item's calories and portion, whether it is
  vegan or vegetarian, and its allergens, as the item says. When filter is set the menu holds only
  the items that match what was asked (matched of on_menu): give them as the answer, grouped by
  station; matched is how many there are, so use it to count or to compare two menus; if matched is
  0 say nothing on that menu matches and name stations_listed, so a station the student called by
  another name can be found. A missing menu with dates_published means the venue has published menus
  only for those dates: say that, as a range when they are consecutive. With documents_published the
  venue posts its menu as a file instead: give each title and link; never list items from it. A menu
  asked about an allergen lists allergens_published, the allergens its labels name: say an allergen
  the labels do not name is not one of them, and that the labels are as the dining service published
  them. allergen_words maps the student's word to the labels it stands for ("dairy" is labelled
  Milk): never say a word is missing when its list has a label. allergens_unlabelled are allergens
  no label on that menu names: the labels cannot show which items are free of them, so never say
  an item is free of one or matches; say the published labels do not cover it. rest_of_menu names
  the meal's other items: when the student also asked what is on the menu, say them too.
  on_other_dates is when a
  food the date's menu lacks is served: say it.
- A derived fact with predicate departures is the posted shuttle timetable worked out for its
  date. Say the departures in trips in order: when each leaves campus (leaves_campus) and its
  route, when it reaches the stop (reached_at), and when it is back (back_on_campus) when given.
  leaves_stop_at is when the same trip picks up at the stop on its way back to campus: always say
  it when given. With back_to_campus each trip is a ride back from the stop: say its pickup there
  and when it is back on campus, never when a shuttle leaves campus.
  which says what was asked. For which all, say where the shuttle goes that day when stops_served
  is given, that there is service that day, its first and
  last departure and how many trips (first_leaves_campus, last_leaves_campus, trip_count), and
  give each trip's time when trips lists them. When none_left is true, say the last
  one left at last_left_at and give next_service (its date and first departure). When
  none_after is true, nothing is listed after the after time, and the day's last still leaves at
  last_leaves_at (it has not left): say both, then next_service.
  by_passed_today means the time the student must be at the stop by has passed today: say so and
  give next_service (its date and trips, or none_by with earliest_reaches_stop).
  departs_from is the stop every trip leaves from (the campus stop): say that is where to catch
  it, as stop_description says.
  stop_description says what the asked stop is and where the shuttle picks up there (pickup):
  give the pickup place when the student asks where to wait or how to find the stop. When
  already_left is true, say it has already left today. When service is none, no shuttle calls
  at that stop (stop) that day; served_on says on which days it does. after (a time) means
  trips is only what leaves campus after it; by means trips is what reaches the stop by that
  time, the last of them being the one to take (none_by: nothing does, and
  earliest_reaches_stop is the earliest). time_unreadable is a time that could not be read; say
  that. When stop_unknown is
  true, say that stop is not on the timetable and name published_stops. route is the route the
  student named; trips are only its trips (route_runs_on: the days it runs, when it does not
  that day). When route_unknown is true, say no route has that name and name published_routes.
  It is the posted schedule, not live: never say a shuttle is running, on time or late.
- A contact note holds many things. A sentence or labelled contact in it that names what the
  student asked about, in their words (media inquiries, parking permits), is the answer: say it
  with its contacts and never leave it out. Leave out the note's other parts and its general
  warnings (an imminent-danger line in a parking answer).
- A schedule's notes belong with its hours (when it opens before an event, by appointment, a
  different time on some days): say the part that bears on what the student asked.
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
- missing means you do not have it: say you do not have verified information about that. With
  reason no_fax_line, say instead that none of the office's published phone lines is a fax.
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
