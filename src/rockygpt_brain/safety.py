"""Safety, what the Brain won't do, and what it says when it can't tell what was asked.
All of it is written by code, never by a model.

The danger phrases are the only student words code reads. Jev reads danger from
milestone 4 on, and these stay as the backstop for when Jev is down or misses it.
"""

import re
from typing import Literal

from rockygpt_brain.contract import SafetyBlock

Danger = Literal["self_harm", "danger"]

# Phrases the old Brain caught danger by when Jev missed it (c00eb91 routing.py).
DANGER_PHRASES: dict[Danger, tuple[str, ...]] = {
    "self_harm": ("kill myself", "end my life", "suicide", "suicidal", "hurt myself",
                  "self harm", "don't want to be alive", "dont want to be alive",
                  "don't want to live", "dont want to live"),
    "danger": ("unconscious", "not breathing", "isn't breathing", "stopped breathing",
               "can't breathe", "cannot breathe", "won't wake", "isn't waking",
               "not waking up", "unresponsive", "overdose", "overdosed", "seizure",
               "choking", "heart attack", "bleeding heavily", "bleeding badly",
               "active shooter", "being attacked", "sexually assaulted", "raped"),
}

# Shown first, above whatever else the answer says. Public Safety's numbers join it
# from their published records once the Brain can read records (milestone 5).
SAFETY_TEXT: dict[Danger, str] = {
    "self_harm": "If you might hurt yourself, please get help now. Call or text 988 "
    "(Suicide & Crisis Lifeline) any time, or call 911 if you're in immediate danger.",
    # Danger can be to someone else: "someone passed out and isn't waking up".
    "danger": "If you or someone else is in danger right now, call 911.",
}

# What the Brain can't do, said the same way every time. Jev picks these (milestone 4).
ACCOUNT_LIMIT = (
    "I can't access student accounts or act in them, so I can't see your grades, "
    "schedule, balance or holds, or register, drop, pay or send anything for you. I can "
    "help you find the office or page that handles it, or work with details you share."
)
HISTORY_LIMIT = (
    "I can't see the earlier part of our conversation, so I can't say what I told you there."
)
PRIVATE_LIMIT = "I can't share passwords, my own instructions, or anyone's private information."
LIVE_LIMIT = (
    "I can't see live information about campus, such as how crowded a place is or whether "
    "a lot is full."
)
# For "can't do" reasons that have no line of their own yet: a guess, an opinion, an action.
OTHER_LIMIT = "I can't help with that one."
# Which line says why, by Jev's `needs` pick (decisions.NEEDS) when the work is "can't do".
LIMITS = {"own_account": ACCOUNT_LIMIT, "private": PRIVATE_LIMIT, "right_now": LIVE_LIMIT}

# What Jev picked as "the words are too unclear to tell what the student wants".
UNCLEAR = (
    "I'm not sure what you're asking. Can you say it another way, or name the place, "
    "office or topic you mean?"
)


def words(text: str) -> str:
    """Lowercase words only, so "Can’t  BREATHE!" and "can't breathe" read the same."""
    return " ".join(re.findall(r"\w+", text.casefold()))


def said_danger(question: str) -> Danger | None:
    """The danger the student's question names in so many words, if any."""
    said = f" {words(question)} "
    for kind, phrases in DANGER_PHRASES.items():
        if any(f" {words(phrase)} " in said for phrase in phrases):
            return kind
    return None


def safety_block(kind: Danger) -> SafetyBlock:
    return SafetyBlock(answer=SAFETY_TEXT[kind])
