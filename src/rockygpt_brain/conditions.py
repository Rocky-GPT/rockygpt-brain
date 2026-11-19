"""Which of a student's conditions a list lookup used, and the sentence that names the rest.

Keep the student's conditions (plan 11-19, Dan chose "Build Steps 0 to 2 on dev"): 12 of the 154
answers in the two Nov 18 test sets dropped a condition the student gave ("home", "til 2", "mon
and tues", "dec 1-4") and said nothing about it. The read fills one record of what was asked
(asked_time.AskedTime); each lookup that picks items from a list (a team's games, the calendar's
events) filters by the conditions it has data for and hands back a receipt: what was asked, what
it used, what it could not use, and what the reads were unsure of. One rule then holds for every
lookup: an answer goes out in code's words only when every condition was used or the answer names
it (engine._code_answer), and the receipt goes in the turn's trace, so a dropped condition can be
counted.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from rockygpt_brain.asked_time import DEFAULT_LABEL, AskedTime, Day, Window, phrase

# The conditions a lookup can be asked to keep. "other:<phrase>" names each condition the
# student gave in their own words, which no list holds.
SLOTS = ("days", "part_of_day", "clock", "home_away", "about")


@dataclass(frozen=True, slots=True)
class Receipt:
    """What a lookup did with the conditions asked. `said` is the sentence the answer must carry
    when a condition was not used or the reads were unsure of one; "" when every one was used."""

    asked: tuple[str, ...] = ()
    used: tuple[str, ...] = ()
    not_used: tuple[str, ...] = ()
    unsure: tuple[str, ...] = ()
    said: str = ""
    # The days and times the lookup used, as the answer says them ("tonight, Tuesday, November
    # 3, after 7 PM"); "" with no window.
    window: str = ""

    @property
    def kept(self) -> bool:
        """Whether every condition asked was used, with nothing unsure."""
        return not self.not_used and not self.unsure

    def as_trace(self) -> dict[str, Any]:
        return {"asked": list(self.asked), "used": list(self.used),
                "not_used": list(self.not_used), "unsure": list(self.unsure),
                **({"window": self.window} if self.window else {}),
                **({"said": self.said} if self.said else {})}


def asked_of(asked: AskedTime | None) -> list[str]:
    """The conditions a record holds, in SLOTS order, then each other condition."""
    if asked is None:
        return []
    found = []
    if asked.when != "none" or asked.break_name != "none" or asked.through != Day() \
            or asked.also:
        found.append("days")
    if asked.part_of_day != "none":
        found.append("part_of_day")
    if asked.clock.relation != "none":
        found.append("clock")
    if asked.home_away != "any":
        found.append("home_away")
    if asked.about:
        found.append("about")
    return found + [f"other:{phrase}" for phrase in asked.other]


def receipt(asked: AskedTime | None, used: Iterable[str], window: Window | None = None,
            where: str = "the listings", slots: Iterable[str] = SLOTS) -> Receipt:
    """The receipt of a lookup that used `used` of the conditions asked (only `slots` are asked
    of it; other conditions always are). Days the window could not place are unsure."""
    allowed = set(slots)
    named = [n for n in asked_of(asked) if n in allowed or n.startswith("other:")]
    unsure = [n for n in (asked.unsure if asked is not None else ())
              if n in allowed or n == "other"]
    if window is not None and window.unplaced and "days" not in unsure:
        unsure.append("days")
    took = set(used)
    kept = tuple(n for n in named if n in took and n not in unsure)
    missed = tuple(n for n in named if n not in took and n not in unsure)
    label = window.label if window is not None else DEFAULT_LABEL
    return Receipt(tuple(named), kept, missed, tuple(unsure),
                   not_checked(missed, tuple(unsure), label, where),
                   phrase(window.label, window.start, window.end, window.days_said)
                   if window is not None else "")


def not_checked(not_used: tuple[str, ...], unsure: tuple[str, ...], label: str,
                where: str = "the listings") -> str:
    """The sentence naming each condition not used or unsure, so a student sees what the answer
    did not check ("I can't check "for commuters" from the listings, so ..."); "" for none."""
    sentences = []
    if "days" in unsure:
        sentences.append(f"I wasn't sure of every day you meant, so this covers {label}.")
    if "clock" in unsure:
        sentences.append("I wasn't sure what time you meant, so this isn't narrowed by time.")
    if "home_away" in unsure:
        sentences.append("I wasn't sure if you meant home or away games, so both are "
                         "listed.")
    if "other" in unsure:
        sentences.append(f"Part of what you asked I couldn't check from {where}.")
    if "days" in not_used:
        sentences.append("This isn't narrowed to the days you asked.")
    if "part_of_day" in not_used or "clock" in not_used:
        sentences.append("This isn't narrowed to the time of day you asked.")
    if "home_away" in not_used:
        sentences.append(f"{where[0].upper()}{where[1:]} don't say which games are home or "
                         "away, so this isn't narrowed by that.")
    if "about" in not_used:
        sentences.append("This isn't narrowed to what you asked about.")
    phrases = [n.split(":", 1)[1] for n in not_used if n.startswith("other:")]
    if phrases:
        named = " or ".join(f'"{p}"' for p in phrases)
        sentences.append(f"I can't check {named} from {where}, so this isn't narrowed by "
                         f"{'it' if len(phrases) == 1 else 'them'}.")
    return " ".join(sentences)
