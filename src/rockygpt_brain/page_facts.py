"""Page answers in two stages (Dan's plan, approved 11-04).

Stage 1 decides what to say: the page sections code read are split into numbered sentences, each
marked with what it holds (a deadline, an amount, a contact, a form or link, a step, a
condition). A small model call made a few times at once returns only the numbers of the
sentences that answer the question, and the sentences most of them pick are kept; it returns
numbers, never words, so it cannot make a fact up. Code then keeps every other sentence of a kind
the question asks for ("when" every deadline, "how much" every amount) in the sections those
picks used. The facts go out as JSON, in page order, with each one's office and source.

Stage 2 only says those facts: a template in the pages' own words, or a model that sees only the
JSON. Code checks the model's reply both ways (every fact's numbers, emails and links are said,
and no number, email, link or he/she is said that no fact holds); a reply that fails gets one
repair try, then the template's text goes out instead.

Why: in the 11-04 check run the lookups, where code chooses the facts, were the same on all five
tries for 12 of 12 questions; page answers, where one model call chose the facts while writing,
for 8 of 44.
"""

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Literal

Writer = Literal["template", "ai"]
WRITERS: tuple[str, ...] = ("template", "ai")

# At most this many facts are said: a three-step appeal with its deadline and its form fits; the
# trace counts how often more were chosen (capped).
MAX_FACTS = 14
# Code adds a sentence of a kind asked only this near a picked one in the same section: the late
# fee after the appeal deadline, not the winter session's limit three sentences on from the fall
# and spring one.
NEAR = 2
# A piece shorter than this ("Fall and Spring", "Winter") is a label, read with what follows it.
MIN_SENTENCE_CHARS = 24
MAX_SENTENCES = 220
MAX_LIST_ITEM_CHARS = 160

_BULLET = re.compile(r"\s*[•▪●◦·]\s+|\s*\n+\s*")
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"”’)]?\s+(?=[A-Z0-9(\"“])")
_NOT_AN_END = re.compile(
    r"(?:\b(?:[A-Za-z]\.){1,3}|\b(?:dr|mr|mrs|ms|prof|st|no|rm|vs|etc|e\.g|i\.e|ext|approx|dept|"
    r"jr|sr|inc|p\.m|a\.m|u\.s)\.)$", re.IGNORECASE)

_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|"
           "december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_KINDS: dict[str, re.Pattern[str]] = {
    "deadline": re.compile(
        r"\b(?:deadlines?|due|no later than|not later than|last day|before the end|by the end|"
        r"(?:within|after|before)\s+\S+\s*(?:\(\d+\)\s*)?(?:business |calendar |school )?"
        r"(?:days?|weeks?|months?)|"
        r"(?:" + _MONTHS + r")\.?\s+\d{1,2}\b|\d{1,2}/\d{1,2}(?:/\d{2,4})?)", re.IGNORECASE),
    "amount": re.compile(
        r"\$\s?\d|\b\d+(?:\.\d+)?\s*(?:%|percent|credits?|credit hours?|courses?|classes|"
        r"days?|weeks?|hours?|semesters?|gpa)\b|\bgpa\b|\b(?:maximum|minimum|up to|at least|"
        r"no more than|more than|fewer than|less than)\s+(?:of\s+)?\(?\d", re.IGNORECASE),
    "contact": re.compile(
        r"[\w.+-]+@[\w-]+\.[\w.]+|\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b|\b(?:room|rm\.?)\s*"
        r"[A-Z]?-?\d{2,4}\b|\b[A-Z]-?\d{3}\b", re.IGNORECASE),
    "link": re.compile(r"https?://|\b(?:forms?|portal|survey|petition|application)\b",
                       re.IGNORECASE),
    "step": re.compile(
        r"^(?:step\s*#?\d|\d+[.)]\s|(?:first|then|next|finally|once|after)\b|(?:submit|complete|"
        r"go|click|log|fill|email|contact|visit|select|enter|request|bring|call|schedule|"
        r"register|download|print|sign|pay|meet|obtain|apply|speak|make|upload|send|stop|ask|"
        r"review|check|choose|return|attach|follow|find|access|navigate|open|search|see|"
        r"use|write)\b)|\bmust\s+(?:first\s+)?(?:request|submit|complete|obtain|file|fill|"
        r"apply|contact|meet|see|pay|register|make|send|provide|be (?:submitted|made|filed))\b",
        re.IGNORECASE),
    "condition": re.compile(r"\b(?:if|unless|only if|only when|eligible|must have|required)\b",
                            re.IGNORECASE),
}

# What a question asks for, by the kinds of sentence that answer it.
_ASKED: dict[str, re.Pattern[str]] = {
    "deadline": re.compile(
        r"\b(?:when|deadlines?|due|how long|by when|too late|in time|last day|still|date|"
        r"dates|until)\b", re.IGNORECASE),
    "amount": re.compile(
        r"\b(?:how much|how many|cost|costs|price|fee|fees|fine|max|maximum|min|minimum|limit|"
        r"credits?|gpa|total|amount|percent)\b", re.IGNORECASE),
    "step": re.compile(
        r"\b(?:how (?:do|can|would|should|to)|steps?|process|apply|request|sign up|appeal|pay|"
        r"submit|file|get)\b", re.IGNORECASE),
    "contact": re.compile(r"\b(?:who|contact|email|e-mail|phone|call|reach|talk to)\b",
                          re.IGNORECASE),
}
_YES_NO = re.compile(r"^\W*(?:(?:so|and|but|also|hey|hi|ok|okay)\W+)?(?:can|could|is|are|do|"
                     r"does|did|will|would|am|may|should|has|have|was|were)\b", re.IGNORECASE)


def sentences(text: str) -> list[str]:
    """A section's text as sentences: each list item or line is one, then split after a full
    stop that ends a sentence (not "Dr.", "Rm.", "a.m."); a piece too short to say anything
    alone ("Fall and Spring") is read with the next."""
    pieces: list[str] = []
    for chunk in _BULLET.split(text or ""):
        chunk = " ".join(chunk.split())
        if not chunk:
            continue
        start = 0
        end = _SENTENCE_END.search(chunk)
        item = chunk[:end.start()] if end else chunk
        if pieces and pieces[-1].endswith((":", ";")) and len(item) <= MAX_LIST_ITEM_CHARS:
            # A list's items go with the line that leads them ("Appeals will not be considered
            # for:"): one fact, not a lead and items read apart. An item that ends its sentence
            # ends the list.
            pieces[-1] += f" {item}" + ("" if item.endswith((".", "!", "?")) else ";")
            if end is None:
                continue
            start = end.end()
        for m in _SENTENCE_END.finditer(chunk):
            before = chunk[start:m.start()].rstrip()
            if _NOT_AN_END.search(before) or re.fullmatch(r"\d{1,2}[.)]", before):
                continue
            pieces.append(before)
            start = m.end()
        pieces.append(chunk[start:].strip())
    merged: list[str] = []
    carry = ""
    for piece in pieces:
        if not piece or piece in ("Δ", "."):
            continue
        piece = f"{carry} {piece}".strip() if carry else piece
        carry = ""
        if len(piece) < MIN_SENTENCE_CHARS:
            carry = piece
            continue
        merged.append(piece)
    if carry:
        if merged and len(carry) < MIN_SENTENCE_CHARS and not re.search(r"\w{3}", carry):
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)
    return merged


def kinds_of(sentence: str) -> list[str]:
    """What a sentence holds, by the marks above, in their order."""
    return [kind for kind, pattern in _KINDS.items() if pattern.search(sentence)]


def asked_kinds(question: str) -> list[str]:
    """The kinds of sentence a question asks for: "when" deadlines, "how much" amounts, "how do I"
    steps and their forms and links, "who" contacts."""
    asked = [kind for kind, pattern in _ASKED.items() if pattern.search(question)]
    return [*asked, *(["link"] if "step" in asked else [])]


def shape_of(question: str) -> str:
    """How the answer opens: yes_no, value (a date, amount or name first), steps, or list; two
    joined by "_then_" when both are asked ("what's the max and can I go over it")."""
    asked = asked_kinds(question)
    shapes = []
    if _YES_NO.search(question):
        shapes.append("yes_no")
    if ({"deadline", "amount"} & set(asked) or re.search(r"\bwho\b", question, re.IGNORECASE)) \
            and "yes_no" not in shapes:
        shapes.append("value")
    if "step" in asked:
        shapes.append("steps")
    return "_then_".join(shapes[:2]) or "list"


def numbered(offices: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every sentence of every section given, numbered from 1 in the order read, each with its
    section's number (the source cited), office and heading, and what it holds. A sentence said
    word for word in an earlier section is given once."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for office in offices:
        for section in office.get("sections") or []:
            text = " ".join(str(section.get(k) or "") for k in ("text", "section_words")
                            if section.get(k))
            for place, said in enumerate(sentences(text)):
                key = re.sub(r"\W+", " ", said.casefold()).strip()
                if key in seen:
                    continue
                seen.add(key)
                found.append({"n": len(found) + 1, "says": said, "kinds": kinds_of(said),
                              "office": office.get("office"), "source": section.get("n"),
                              "heading": section.get("heading"), "place": place,
                              **({"past_term": True} if section.get("past_term") else {}),
                              **({"applies_to": section["applies_to"]}
                                 if section.get("applies_to") else {})})
                if len(found) >= MAX_SENTENCES:
                    return found
    return found


PICK_PROMPT = """A Ramapo College student asked a question. Below are numbered sentences from
the college's own pages, grouped by office and section heading. Choose the sentences the answer
needs: list each part the student asked (asked); whether it asks for something no college page
holds, such as the student's own records, something live right now, or an opinion
(not_on_pages); and the numbers of every sentence that answers it (sentences): each step, form,
link, deadline, amount, condition and exception for it. Leave out sentences about other groups,
programs, places, terms or topics, and sentences that only say the same thing again. When
already_written is given, it already answers part of the question: choose sentences only for
what else is asked. A part no sentence answers gets an empty list. Answer only with the
pick_facts tool."""

PICK_TOOL: dict[str, Any] = {
    "type": "function", "name": "pick_facts", "strict": True,
    "description": "The parts asked and the numbers of the sentences that answer each.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["parts"],
                   "properties": {"parts": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False,
                       "required": ["asked", "not_on_pages", "sentences"],
                       "properties": {
                           "asked": {"type": "string"},
                           "not_on_pages": {"type": "boolean"},
                           "sentences": {"type": "array", "items": {"type": "integer"}}}}}}},
}


def pick_messages(question: str, earlier: str | None, lines: Sequence[Mapping[str, Any]],
                  already_written: Sequence[str] = ()) -> list[dict[str, Any]]:
    """The question and the numbered sentences, each section's under its office and heading."""
    out: list[str] = []
    last: tuple[Any, Any] | None = None
    for line in lines:
        if (line["office"], line["heading"]) != last:
            last = (line["office"], line["heading"])
            out.append(f"\n## {line['office']} | {line['heading']}"
                       + (" (past term)" if line.get("past_term") else ""))
        out.append(f"{line['n']}. {line['says']}")
    given = [f"Question: {question}", *([f"Earlier question: {earlier}"] if earlier else []),
             *([f"already_written:\n{chr(10).join(already_written)}"] if already_written else []),
             "Sentences:" + "\n".join(out)]
    return [{"role": "system", "content": PICK_PROMPT},
            {"role": "user", "content": "\n\n".join(given)}]


def _numbers(pick: Mapping[str, Any] | None, size: int) -> list[int]:
    if not isinstance(pick, Mapping):
        return []
    found: list[int] = []
    for part in pick.get("parts") or []:
        if isinstance(part, Mapping):
            found.extend(n for n in part.get("sentences") or []
                         if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= size)
    return list(dict.fromkeys(found))


def agreed(picks: Sequence[Mapping[str, Any] | None], size: int) -> list[int] | None:
    """The sentences more than half the picks that came back chose, by how many chose each and
    then by number; None when no pick came back."""
    valid = [_numbers(p, size) for p in picks if isinstance(p, Mapping)]
    if not valid:
        return None
    votes: dict[int, int] = {}
    for chosen in valid:
        for n in chosen:
            votes[n] = votes.get(n, 0) + 1
    return sorted((n for n, count in votes.items() if count > len(valid) // 2),
                  key=lambda n: (-votes[n], n))


def chosen_parts(picks: Sequence[Mapping[str, Any] | None], kept: Sequence[int],
                 lines: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The first pick's parts, each with the sections of its sentences that were kept, in the
    shape one_call.gave_up reads (a part with no section is looked for again)."""
    first = next((p for p in picks if isinstance(p, Mapping)), None) or {}
    source = {line["n"]: line["source"] for line in lines}
    parts = []
    for part in first.get("parts") or []:
        if not isinstance(part, Mapping):
            continue
        mine = [n for n in part.get("sentences") or [] if n in kept]
        parts.append({"asked": str(part.get("asked") or ""),
                      "not_on_pages": bool(part.get("not_on_pages")),
                      "sections": list(dict.fromkeys(source[n] for n in mine))})
    return parts


def facts_of(question: str, lines: Sequence[Mapping[str, Any]], picked: Sequence[int],
             today: date, already_written: Sequence[str] = (),
             computed: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Stage 1's JSON: the sentences picked, then every other sentence of a kind the question
    asks for near a picked one in its section (NEAR), at most MAX_FACTS (picked ones first),
    said in page order; with the answer's shape. Code's computed facts about the sections used
    (a date that has passed) come with them."""
    by_n = {line["n"]: line for line in lines}
    used = {by_n[n]["source"] for n in picked if n in by_n}
    near = {(by_n[n]["source"], by_n[n]["place"] + step) for n in picked if n in by_n
            for step in range(-NEAR, NEAR + 1)}
    asked = set(asked_kinds(question))
    by_code = [line["n"] for line in lines
               if (line["source"], line["place"]) in near and line["n"] not in picked
               and asked & set(line["kinds"])]
    order = [*[n for n in picked if n in by_n], *by_code]
    kept = set(order[:MAX_FACTS])
    facts = [{"n": 0, "kind": next((k for k in line["kinds"] if k in asked),
                                   line["kinds"][0] if line["kinds"] else "statement"),
              "says": line["says"], "office": line["office"], "source": line["source"],
              "chosen_by": "ai" if line["n"] in picked else "code",
              **({"past_term": True} if line.get("past_term") else {}),
              **({"applies_to": line["applies_to"]} if line.get("applies_to") else {})}
             for line in lines if line["n"] in kept]
    for number, fact in enumerate(facts, start=1):
        fact["n"] = number
    notes = [{"says": str(c.get("fact")), "source": c.get("n")} for c in computed
             if c.get("fact") and c.get("n") in used]
    return {"question": question, "today": today.isoformat(), "shape": shape_of(question),
            "facts": facts, **({"code_notes": notes} if notes else {}),
            **({"already_written": list(already_written)} if already_written else {}),
            **({"capped": len(order) - MAX_FACTS} if len(order) > MAX_FACTS else {})}


def template_text(page: Mapping[str, Any]) -> str:
    """The facts in the pages' own words: each office named, then its facts, each section's
    sentences together with the section's mark; a step-by-step answer numbers its steps."""
    facts = page.get("facts") or []
    if not facts:
        return ""
    stepped = {office for office in {f["office"] for f in facts}
               if sum(f["office"] == office and f["kind"] == "step" for f in facts) >= 2}
    steps = "steps" in str(page.get("shape") or "")
    blocks: list[str] = []
    office: Any = object()
    paragraph: list[str] = []
    source: Any = None
    number = 0

    def close() -> None:
        if paragraph:
            blocks[-1] += ("\n" if blocks[-1] else "") + " ".join(paragraph) + f" [{source}]"
            paragraph.clear()

    for fact in facts:
        if fact["office"] != office:
            close()
            office = fact["office"]
            blocks.append(f"**{office}**")
            number = 0
        said = fact["says"].rstrip()
        said = said if said[-1:] in ".!?:" else said + "."
        if fact.get("past_term"):
            said = f"{said} (This is for a term that is over.)"
        if steps and fact["kind"] == "step" and fact["office"] in stepped:
            close()
            number += 1
            blocks[-1] += f"\n{number}. {said} [{fact['source']}]"
            continue
        if fact["source"] != source:
            close()
        source = fact["source"]
        paragraph.append(said)
    close()
    notes = [f"{n['says']}" for n in page.get("code_notes") or []]
    return "\n\n".join([*blocks, *notes]).strip()


WRITER_PROMPT = """You write a Ramapo College student's answer from facts that code chose from
the college's own pages, given as JSON. You know nothing else.
- Say every fact once, in plain short words, in the order given, and nothing else: never add a
  fact, number, date, name, email, link, step or advice the facts do not hold, and never say
  he or she about anyone unless a fact does.
- Keep each fact's numbers, dates, amounts, emails, phone numbers, rooms, form names and links
  exactly as written. A fact marked past_term is for a term that is over: say so.
- shape says how to open: yes_no starts with Yes, No or It depends, only as the facts say;
  value gives the date, amount or name asked first; steps numbers the steps.
- Name each office before its facts. code_notes are code's notes on the facts' dates: say them
  as given. When already_written is given it is placed before your text: never repeat it.
- Code adds each office's phone, email and room after your text: do not list them yourself
  unless a fact gives them.
- Cite each statement with its fact's source mark, like [3]. Never write "fact 2". No greeting.
  Under 1,600 characters.
Answer only with the write_answer tool."""

WRITE_TOOL: dict[str, Any] = {
    "type": "function", "name": "write_answer", "strict": True,
    "description": "The reply to the student.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["text"],
                   "properties": {"text": {"type": "string", "maxLength": 6000}}},
}


def writer_messages(page: Mapping[str, Any], misses: Mapping[str, Any] | None = None,
                    last: str | None = None) -> list[dict[str, Any]]:
    """The facts JSON for the AI writer; with the last reply and what the check found in it, for
    its one repair try."""
    given = {k: v for k, v in page.items() if k != "capped"}
    given["facts"] = [{k: v for k, v in f.items() if k != "chosen_by"}
                      for f in page.get("facts") or []]
    messages = [{"role": "system", "content": WRITER_PROMPT},
                {"role": "user", "content": json.dumps(given, ensure_ascii=False)}]
    if misses and last is not None:
        said = []
        if misses.get("unsaid"):
            said.append("It left out these facts (by n): "
                        + ", ".join(str(n) for n in misses["unsaid"]) + ".")
        if misses.get("extra"):
            said.append("It said these, which no fact holds: "
                        + "; ".join(misses["extra"]) + ".")
        messages.append({"role": "user", "content": "Your last reply was:\n" + last + "\n\n"
                         + " ".join(said) + " Write it again, saying every fact and nothing "
                         "else."})
    return messages


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)
_PHONE = re.compile(r"\(?\b(\d{3})\)?[\s.-]*(\d{3})[\s.-]*(\d{4})\b")
_NUMBER = re.compile(r"(?<![\w.])\$?\d+(?:[.,:]\d+)*")
_MARK = re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]")
_LIST_NUMBER = re.compile(r"(?m)^\s*\d{1,2}[.)]\s")
_PRONOUN = re.compile(r"\b(?:he|she|him|his|her|hers)\b", re.IGNORECASE)
_WORD_NUMBERS = dict(zip(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty ninety".split(),
    [*range(1, 21), 30, 40, 50, 60, 90], strict=True))


def tokens(text: str) -> set[str]:
    """The checkable things a text says: emails, links, phone numbers (by digits) and numbers
    (a dollar sign, commas and a trailing ".0" aside); a number written as a word counts as its
    digits."""
    text = _MARK.sub(" ", text)
    found = {e.casefold().rstrip(".") for e in _EMAIL.findall(text)}
    found |= {u.casefold().rstrip(".,;:") for u in _URL.findall(text)}
    rest = _URL.sub(" ", _EMAIL.sub(" ", text))
    found |= {"".join(p) for p in _PHONE.findall(rest)}
    rest = _PHONE.sub(" ", rest)
    for number in _NUMBER.findall(rest):
        plain = number.lstrip("$").replace(",", "")
        found.add(plain[:-2] if plain.endswith(".0") else plain)
    found |= {str(_WORD_NUMBERS[w]) for w in re.findall(r"[a-z]+", rest.casefold())
              if w in _WORD_NUMBERS}
    return found


def misses(text: str, page: Mapping[str, Any], allowed: str = "") -> dict[str, Any]:
    """What the AI writer's reply got wrong: the facts whose numbers, emails, links or phone
    numbers it does not say, or whose source it never cites (unsaid, by n); and the numbers,
    emails, links and he/she it says that no fact, note, question or code-written part holds
    (extra)."""
    said = tokens(_LIST_NUMBER.sub(" ", text))
    cited = {int(n) for group in re.findall(r"\[([\d,\s–-]+)\]", text)
             for n in re.findall(r"\d+", group)}
    facts = page.get("facts") or []
    unsaid = [f["n"] for f in facts
              if not tokens(f["says"]) <= said or f["source"] not in cited]
    held_text = " ".join([*(f["says"] for f in facts),
                          *(n["says"] for n in page.get("code_notes") or []),
                          str(page.get("question") or ""),
                          *(page.get("already_written") or []), allowed])
    held = tokens(held_text)
    extra = sorted(said - held)
    if _PRONOUN.search(_MARK.sub(" ", text)) and not _PRONOUN.search(held_text):
        extra.append("he or she")
    return {**({"unsaid": unsaid} if unsaid else {}), **({"extra": extra} if extra else {})}
