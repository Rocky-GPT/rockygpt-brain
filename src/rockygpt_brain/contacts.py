"""An office's contacts said by code, from the campus directory (Dan 11-03: code writes as much of
each answer as it can, so the same question gets the same answer).

A question that only asks how to reach offices it names ("how do I reach the health center and
counseling") is answered by code alone: each office's phone numbers, email and room as the
directory publishes them. A question that asks who to go to ("my bike got stolen, who do I report
it to") is answered by the model from the offices' pages, but the contacts of the offices its
reply cites are written by code after it."""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from rockygpt_brain.answers import us_phone
from rockygpt_brain.plain_writer import literal

# At most this many offices' contacts are said: more named is a list question, not a contact one.
MAX_OFFICES = 3

# The last word of an office's name that says only that it is an office: "Counseling Center" is
# also asked for as "counseling".
_GENERIC_END = re.compile(r"\s+(?:center|services?|office|department)$", re.IGNORECASE)

# Every word a question may hold besides the offices' names and still ask only how to reach them.
_REACH_WORDS = frozenset("""
how do does can could would should i we reach contact call phone phones telephone number numbers
email e-mail mail emails get hold touch in with of the a an and or for to at what whats what's is
are their its it's s office offices room rooms address contact contacts info information details
where wheres where's located location find me my please ramapo college campus 's
""".split())
_ASKS_REACH = re.compile(r"\b(?:reach|contact|call|phone|number|e-?mail|get (?:a )?hold of|"
                         r"get in touch|where)\b", re.IGNORECASE)

# Who to go to: the offices the reply sends the student to get their contacts written by code.
_ASKS_WHO = re.compile(
    r"\b(?:who|where|which office) (?:do|should|can|would) (?:i|we) (?:report|talk|speak|go|ask|"
    r"contact|call|tell|turn)\b|\bwho (?:handles|deals with|takes care of)\b"
    r"|\b(?:who|where) (?:to|do i) (?:report|contact|call)\b", re.IGNORECASE)


def _names(office: Mapping[str, Any]) -> list[str]:
    name = str(office.get("name") or "")
    plain = re.sub(r"\s*\([^)]*\)", "", name).strip()
    return [n for n in dict.fromkeys([name, plain, *(office.get("aliases") or [])])
            if len(str(n)) >= 4]


def _core(name: str) -> str | None:
    """An office's name without its generic last word ("Counseling" of "Counseling Center")."""
    plain = re.sub(r"\s*\([^)]*\)", "", name).strip()
    core = _GENERIC_END.sub("", plain)
    return core if core != plain and len(core) >= 5 else None


def offices_named(offices: Sequence[Mapping[str, Any]], message: str,
                  ) -> tuple[list[dict[str, Any]], list[str]]:
    """The offices a message names by a published name or other name, or by its name less a
    generic last word when no other office's name starts with that; with the words that named
    them. An office named only inside a longer office name the message holds is not named."""
    starts: dict[str, int] = {}
    for office in offices:
        first = str(office.get("name") or "").casefold().split()
        for k in range(1, len(first) + 1):
            prefix = " ".join(first[:k])
            starts[prefix] = starts.get(prefix, 0) + 1
    spans: list[tuple[int, int, str, Mapping[str, Any]]] = []
    for office in offices:
        said = _names(office)
        core = _core(str(office.get("name") or ""))
        if core and starts.get(core.casefold(), 0) <= 1:
            said.append(core)
        for name in said:
            for m in re.finditer(rf"(?<![\w&]){re.escape(str(name))}(?![\w&])", message,
                                 re.IGNORECASE):
                spans.append((m.start(), m.end(), m.group(0), office))
    kept = [s for s in spans if not any(o[0] <= s[0] and s[1] <= o[1] and o[1] - o[0] > s[1] - s[0]
                                        for o in spans)]
    found: dict[str, dict[str, Any]] = {}
    words: list[str] = []
    for _, _, text, office in sorted(kept, key=lambda s: s[0]):
        found.setdefault(str(office["id"]), dict(office))
        words.append(text)
    return list(found.values()), words


def plain_name(name: str) -> str:
    """An office's name without its parenthesised part: "Public Safety (Emergency)" is
    "public safety"."""
    return re.sub(r"\s*\([^)]*\)", "", name).strip().casefold()


def with_siblings(chosen: Sequence[Mapping[str, Any]], offices: Sequence[Mapping[str, Any]],
                  ) -> list[dict[str, Any]]:
    """The chosen offices, each followed by the offices that share its name but for a part in
    parentheses ("Public Safety (Emergency)" beside "Public Safety (Non-Emergency)"): their
    names say which line is for what, so a reply that sends the student to one shows both
    (11-03: the steps said 684-6666 and the card 684-7432, with nothing to tell them apart)."""
    out: dict[str, dict[str, Any]] = {}
    for office in chosen:
        out.setdefault(str(office["id"]), dict(office))
        plain = plain_name(str(office.get("name") or ""))
        for other in offices:
            if plain and plain_name(str(other.get("name") or "")) == plain:
                out.setdefault(str(other["id"]), dict(other))
    return list(out.values())


def without_variant_names(written: str, offices: Sequence[Mapping[str, Any]]) -> str:
    """The reply with each office that has a sibling among `offices` called by the name they
    share: the contact lines after it say which line is Emergency and which Non-Emergency, so
    the words must not pin one number on the other's name (11-03: "Report it to Public Safety
    (Non-Emergency)... Call (201) 684-6666", the emergency line)."""
    plains = [plain_name(str(o.get("name") or "")) for o in offices]
    for office, plain in zip(offices, plains, strict=True):
        name = str(office.get("name") or "")
        if plain and plains.count(plain) > 1 and name.casefold() != plain:
            shared = re.sub(r"\s*\([^)]*\)", "", name).strip()
            written = re.sub(re.escape(name), shared, written, flags=re.IGNORECASE)
    return written


def asks_contact_only(message: str, named_words: Iterable[str]) -> bool:
    """Whether a message asks only how to reach (or where to find) the offices it names."""
    rest = message
    for name in sorted(set(named_words), key=len, reverse=True):
        rest = re.sub(rf"(?i)(?<![\w&]){re.escape(name)}(?![\w&])", " ", rest)
    words = re.findall(r"[a-z'’-]+", rest.casefold().replace("’", "'"))
    return bool(_ASKS_REACH.search(rest)) and all(w in _REACH_WORDS for w in words)


def asks_who(message: str) -> bool:
    """Whether a message asks who or which office to go to ("who do I report it to")."""
    return bool(_ASKS_WHO.search(message))


_ROOM = re.compile(r"^[A-Z]{1,3}-?\d{2,4}[A-Z]?$")


def _phones(values: Sequence[Any]) -> list[str]:
    numbers = [p for group in values for p in (group if isinstance(group, list) else [group])
               if isinstance(p, dict) and p.get("number")]
    # The office's own numbers; its staff's, its director's and its fax only when it has none.
    main = [us_phone(str(p["number"])) for p in numbers if not p.get("type")]
    typed = [f"{literal(p['type'])} {us_phone(str(p['number']))}" for p in numbers
             if p.get("type")]
    return list(dict.fromkeys(main or typed))


def contact_text(name: str, emails: Sequence[Any], phones: Sequence[Any], rooms: Sequence[Any],
                 n: int) -> str | None:
    """One office's published contacts in code's words, or None when it publishes none:
    "**Counseling Center**: Phone: (201) 684-7522. Office: D-216. [3]"."""
    said = []
    if numbers := _phones(phones):
        said.append(f"Phone: {'; '.join(numbers)}.")
    if mails := [str(e) for e in emails if isinstance(e, str) and "@" in e]:
        said.append(f"Email: {', '.join(dict.fromkeys(mails))}.")
    places = [str(r).strip().rstrip(".") for group in rooms
              for r in (group if isinstance(group, list) else [group]) if str(r).strip()]
    for place in dict.fromkeys(places):
        said.append(f"{'Office' if _ROOM.match(place) else 'Location'}: {literal(place)}.")
    return f"**{literal(name)}**: {' '.join(said)} [{n}]" if said else None
