"""The verifier: a reply may say only what its evidence holds.

The Brain's job ends at the validated Fact Packet and its writer view (writer_view.py). Code says
a lookup's facts from that view (plain_writer.py; the AI lookup writer that was here was removed
with audit plan item 13, 11-17), and the page writers' replies are checked the same way
(one_call.py). This module is the check that the words stay inside the evidence.

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
# 4,000: a how-to answer gives an office's steps in full; at 2,400 a third of how-to replies
# were cut off (blind how-to set, 10-19) and the code's shorter text went out instead.
WRITER_TEXT_LIMIT = 4000

_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
# A citation mark right after an address ("…/faqs/.[1]") is not part of it.
_URL = re.compile(r"https?://[^\s()\[\]<>\"']+")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_NUMBER = re.compile(r"\d+")
_COURSE = re.compile(r"\b([a-z]{2,6}) ?(\d{3}[a-z]?)\b")
# A course code as the catalog writes it, its subject in capitals ("CMPS 147", "MATH 353").
_COURSE_CODE = re.compile(r"\b([A-Z]{2,6}) ?(\d{3}[A-Z]?)\b")
_LIST_ITEM = re.compile(r"(?m)^[ \t]*\d{1,2}[.)][ \t]")
# A citation mark: one source's number ("[3]"), or several written together ("[3, 4]", "[4–6]").
# Models group them though asked for one: on 11-02 15 of 100 replies had grouped marks, read as
# citing nothing, so their sources were never shown.
_MARKER = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")
MAX_MARK_RANGE = 12


def marked(mark: str) -> list[int]:
    """The source numbers a mark's inside stands for: "3, 4" is 3 and 4, "4–6" is 4 to 6."""
    numbers: list[int] = []
    for piece in mark.split(","):
        ends = [int(n) for n in re.split(r"\s*[–-]\s*", piece.strip()) if n]
        if len(ends) == 2 and 0 < ends[1] - ends[0] <= MAX_MARK_RANGE:
            numbers.extend(range(ends[0], ends[1] + 1))
        else:
            numbers.extend(ends)
    return numbers


def cited(text: str) -> list[int]:
    """Every source number the text's citation marks stand for, once each, in order."""
    return list(dict.fromkeys(n for m in _MARKER.finditer(text) for n in marked(m.group(1))))


# A number the pages spell out ("within twenty calendar days") is the same number a reply writes
# as digits ("20 calendar days"); on 11-01 two good grade-appeal replies were thrown away for it.
_NUMBER_WORDS = {
    word: value for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen".split())}
_NUMBER_WORDS.update({"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
                      "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
                      "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
                      "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
                      "twice": 2, "once": 1, "dozen": 12})
_TENS = ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_SPELLED = re.compile(r"\b(" + "|".join(_TENS) + r")[\s-]+(one|two|three|four|five|six|seven|"
                      r"eight|nine)\b|\b(" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
                      + r")\b")
# Dashes and spaces a model writes inside a phone number or a range ("684‑7495", "9–5").
_DASHES = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}
                        | {c: " " for c in "\u00a0\u2007\u202f\u2009"})


def _spelled_numbers(text: str) -> set[str]:
    found: set[str] = set()
    for m in _SPELLED.finditer(text):
        if m.group(1):
            found.add(str(_NUMBER_WORDS[m.group(1)] + _NUMBER_WORDS[m.group(2)]))
        else:
            found.add(str(_NUMBER_WORDS[m.group(3)]))
    return found


def unsupported_by(text: str, view: dict[str, Any]) -> list[str]:
    """What in the reply the view does not hold; empty means the reply may be sent."""
    held = json.dumps(view, ensure_ascii=False).casefold().translate(_DASHES)
    digits = re.sub(r"\D", "", held)
    # 07 in a date and the 7 a person writes are the same number.
    numbers = {str(int(n)) for n in _NUMBER.findall(held)} | _spelled_numbers(held)
    sources = {str(source["n"]) for source in view.get("sources", [])}
    rest = text.casefold().translate(_DASHES)
    problems: list[str] = []
    for email in _EMAIL.findall(rest):
        if email not in held:
            problems.append(f"an email address the facts do not hold: {email}")
    rest = _EMAIL.sub(" ", rest)
    for url in _URL.findall(rest):
        # A trailing slash names the same page: ".../report-an-incident-form/" was refused when
        # the page wrote ".../report-an-incident-form", and the whole reply with it (11-03).
        bare = url.rstrip(".,;:").rstrip("/")
        if bare not in held and f"{bare}/" not in held:
            problems.append(f"a web address the facts do not hold: {url}")
    rest = _URL.sub(" ", rest)
    for phone in _PHONE.findall(rest):
        number = re.sub(r"\D", "", phone)[-10:]
        if number not in digits:
            problems.append(f"a phone number the facts do not hold: {phone.strip()}")
    rest = _PHONE.sub(" ", rest)
    # A course code of a subject the facts hold ("CHEM 425") must be one of their codes. Codes
    # are read as the catalog writes them, in capitals: read casefolded, the "or 370" of "MATH 353
    # STATISTICS or 370 APPLIED STATISTICS" was a course "OR 370" that the facts do not hold, and
    # a whole CS major answer was thrown away for it (11-03 repeat test). Any other word before
    # a number leaves the number to the number check below.
    codes = {f"{m.group(1)} {m.group(2)}"
             for m in _COURSE_CODE.finditer(json.dumps(view, ensure_ascii=False))}
    subjects = {code.split()[0] for code in codes}
    checked: set[str] = set()
    for m in _COURSE_CODE.finditer(_URL.sub(" ", text)):
        if m.group(1) in subjects:
            code = f"{m.group(1)} {m.group(2)}"
            checked.add(code.casefold())
            if code not in codes:
                problems.append(f"a course the facts do not hold: {code}")
    rest = _COURSE.sub(lambda m: " " if f"{m.group(1)} {m.group(2)}" in checked else m.group(0),
                       rest)
    rest = _MARKER.sub(lambda m: " " if all(str(n) in sources for n in marked(m.group(1)))
                       else m.group(0), rest)
    # A numbered step ("2. Submit the form") numbers the reply's own list, not a fact.
    rest = _LIST_ITEM.sub(" ", rest)
    for number in _NUMBER.findall(rest):
        if str(int(number)) not in numbers:
            problems.append(f"a number the facts do not hold: {number}")
    return problems


def says_something(view: dict[str, Any]) -> bool:
    """Whether the view holds facts for a writer to say (not only a question or fixed wording)."""
    return any(view.get(key) for key in ("facts", "derived_facts", "not_published", "missing"))
