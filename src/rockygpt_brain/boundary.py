"""Step 2, safety and capability boundaries: the first look at every Turn.

Code alone reads the message and code alone writes every reply here. No model is involved, so
the same message always takes the same path:

- `safety`: someone may be in immediate danger. Danger wins over everything else.
- `capability_limit`: the student's own account, or something RockyGPT would have to do for
  them. RockyGPT can't see or change either.
- `continue`: anything else goes on to the next step.

The phrases are a short floor, not a reading of the message: unusual wording gets past them.
A wrong `safety` only shows 911 help nobody needed, so those phrases are the widest.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from rockygpt_brain.turn import Turn

SAFETY_MESSAGE = (
    "If you or someone else is in danger right now, call 911. "
    "If you are thinking about hurting yourself, call or text 988 (Suicide & Crisis Lifeline) "
    "to talk with someone right away. "
    "RockyGPT can't send help or stay with you, so please reach out to them now."
)
CAPABILITY_MESSAGE = (
    "I can't see or change your personal student information, and I can't take actions for "
    "you, like registering for a class, dropping one or making a payment. Please use your own "
    "student account, or ask the campus office that handles it."
)


@dataclass(frozen=True, slots=True)
class BoundaryResult:
    kind: Literal["continue", "safety", "capability_limit"]
    message: str = ""


CONTINUE = BoundaryResult("continue")

_DANGER = re.compile("|".join((
    r"\b(kill|hurt|harm|cut) my ?self\b", r"\bsuicid", r"\bwant to die\b", r"\bend my life\b",
    r"\b(not|isnt|stopped|cant|cannot) breath(e|ing)\b",
    r"\b(collapsed|unconscious|unresponsive|passed out|seizure|choking|overdos\w*|heart attack)\b",
    r"\bbleeding (badly|heavily)\b", r"\b(911|ambulance)\b",
    r"\b(shooter|gunman|has a gun|has a knife)\b",
    r"\bon fire\b", r"\bgas leak\b", r"\bsmell (smoke|gas)\b",
    r"\b(being|got) (attacked|assaulted|followed)\b", r"\b(raped|sexually assaulted)\b",
    r"\ban emergency\b",
)))
_HOW_TO = re.compile(r"^(how (do|can|to)|where (do|can))\b")
_MY_ACCOUNT = re.compile(
    r"\bmy (grades?|gpa|transcripts?|schedule|classes|balance|bill|tuition|financial aid|"
    r"holds?|registration|account|password)\b"
    r"|^(?:(?:can|could|will) you )?(register|enroll|drop|withdraw|add|pay|sign up|apply|"
    r"cancel|reset)\b.{0,25}?\b(me|my)\b"
)


def _plain(text: str) -> str:
    """Lower case, no accents or apostrophes, punctuation as spaces."""
    text = re.sub(r"['’`´]", "", text)
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(c for c in text if unicodedata.category(c) not in ("Mn", "Cf"))
    return re.sub(r"[\W_]+", " ", text).strip()


def check(turn: Turn) -> BoundaryResult:
    """Read the Turn's message. Only the latest message counts, not earlier ones."""
    text = _plain(turn.message)
    if _DANGER.search(text):
        return BoundaryResult("safety", SAFETY_MESSAGE)
    if not _HOW_TO.match(text) and _MY_ACCOUNT.search(text):
        return BoundaryResult("capability_limit", CAPABILITY_MESSAGE)
    return CONTINUE
