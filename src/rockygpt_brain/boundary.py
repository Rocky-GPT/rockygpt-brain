"""Immediate, model-independent danger help.

This phrase floor is not a semantic safety classifier. The primary assistant also reads the
bounded context. Account access is absent from the tool set; phrases about "my" records must
not discard answerable public parts of a request.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from rockygpt_brain.turn import Turn

# What kind of emergency it is decides which help comes first. "other" is the general text, used
# when the kind is unknown, when several kinds apply, and for every failure.
SITUATIONS = ("self_harm", "medical", "danger", "fire", "other")
SAFETY_TEXTS = {
    "self_harm": (
        "You don't have to go through this alone. Call or text 988 (Suicide & Crisis Lifeline) "
        "right now to talk with someone, any time of day. You can also call it if you are "
        "worried about someone else. If you or someone else is in danger right now, call 911. "
        "RockyGPT can't send help or stay with you, so please reach out to them now."
    ),
    "medical": (
        "Call 911 right now and tell them where you are and what happened. They can tell you "
        "what to do until help arrives. If it's safe, stay with the person. "
        "RockyGPT can't send help or stay with you, so please make that call first."
    ),
    "danger": (
        "If you are in danger right now, call 911. If you can do it safely, get away from the "
        "danger and tell them where you are. "
        "RockyGPT can't send help or stay with you, so please call now."
    ),
    "fire": (
        "Get out and away from the fire, smoke or gas right now, and don't go back in for "
        "anything. Then call 911 from a safe place. "
        "RockyGPT can't send help or stay with you, so please call as soon as you are out."
    ),
    "other": (
        "If you or someone else is in danger right now, call 911. "
        "If you are thinking about hurting yourself, call or text 988 (Suicide & Crisis Lifeline) "
        "to talk with someone right away. "
        "RockyGPT can't send help or stay with you, so please reach out to them now."
    ),
}
SAFETY_MESSAGE = SAFETY_TEXTS["other"]


def safety_text(situation: str | None) -> str:
    return SAFETY_TEXTS.get(situation or "other", SAFETY_MESSAGE)


def situation_of(text: str) -> str | None:
    """Which emergency text a reply starts with, or None when it starts with none of them."""
    return next((name for name, known in SAFETY_TEXTS.items() if text.startswith(known)), None)


_CAPABILITY_LIMIT = (
    "I can't see or change your personal student information, and I can't take actions for "
    "you, like registering for a class, dropping one or making a payment. Please use your own "
    "student account, "
)
CAPABILITY_MESSAGE = _CAPABILITY_LIMIT + "or ask the campus office that handles it."
# Used when an office's published details are already shown above it.
CAPABILITY_AFTER_LOOKUP_MESSAGE = _CAPABILITY_LIMIT + "or contact the office above."


@dataclass(frozen=True, slots=True)
class BoundaryResult:
    kind: Literal["continue", "safety"]
    message: str = ""
    situation: str | None = None


CONTINUE = BoundaryResult("continue")

# A hazard word followed by one of these is a question about a program or a rule, not a hazard.
_NOT_A_HAZARD = (
    r"(?! (?:\w+ )?(?:drills?|training|policy|policies|protocol|procedures?|plans?|prevention|"
    r"awareness|safety|clubs?|workshops?|classes|class|courses?|programs?|rules?|alarms?|"
    r"extinguishers?|exits?|inspections?|evacuations?|regulations?|detectors?|meetings?))"
)
_SELF = r"(my ?self|him ?self|her ?self|them ?selves|themselves)"
_DANGER: dict[str, list[re.Pattern[str]]] = {
    situation: [re.compile(p) for p in patterns]
    for situation, patterns in {
        "self_harm": (
            rf"\b(kill|hurt|harm|cut|injur|stab|shoot|hang)\w* {_SELF}\b",
            r"\b(end|take) my (own )?life\b",
            rf"\bsuicid\w*\b{_NOT_A_HAZARD}",
            r"\b(want|wanna|going|plan|planning) to die\b",
            r"\b(dont|do not) (want to|wanna) (live|be alive|exist|be here|go on|keep going)",
            r"\b(end it all|better off dead|not worth living|no reason to live)\b",
            r"\bjump(ing)? (off|from) (the |a |my )?"
            r"(roof|bridge|building|garage|parking garage|balcony)\b",
        ),
        "medical": (
            r"\b(not|isnt|arent|stopped|stop|cant|cannot|wont|hardly|barely) breath(e|ing)\b",
            r"\b(trouble|difficulty) breathing\b|\bunable to breathe\b",
            r"\b(wont|cant|couldnt|cannot|not|isnt|barely) (wake|waking|respond|responding|moving|"
            r"responsive|conscious)\b",
            r"\b(unconscious|unresponsive|passed out|blacked out|collapsed|fainted|choking|"
            r"heart attack|having a stroke|chest pain)\b",
            rf"\bseizure{_NOT_A_HAZARD}",
            r"\b(turning|turned|lips are) blue\b",
            r"\bbleeding (from|out|badly|a lot|heavily|profusely|through)\b|"
            r"\b(cant|cannot|wont|will not) stop bleeding\b|"
            r"\bblood (everywhere|all over|pouring)\b",
        ),
        "danger": (
            rf"\b(active shooter|shooter|gunman|bomb threat){_NOT_A_HAZARD}",
            r"\b(gunshots?|shots fired|hostage)\b",
            r"\bshoot(ing)? up\b|\bthreaten\w* to (shoot|kill|stab|bomb|hurt|attack)\b",
            r"\b(has|have|with|holding|pulled|pointed) (a |an )?(gun|knife|weapon)\b",
            r"\b(gonna|going to|will|wants to|want to) (hurt|kill|hit|beat|attack|stab|shoot|rape) "
            r"(me|us|him|her|them|people|everyone)\b",
            r"\b(being|getting|got|been|was) (just )?(attacked|assaulted|robbed|stalked|followed|"
            r"chased|threatened|jumped|mugged|beaten|kidnapped|stabbed|shot|choked)\b",
            r"\b(attacking|hurting|threatening|stalking|following|chasing|shooting|hitting|"
            r"beating|hit|punched|slapped|choked|grabbed|dragged|pinned|trapped|locked|cornered|"
            r"kidnapped) me\b",
            r"\b(was|been|got)( just| recently| again)? (raped|sexually assaulted|molested)\b",
            r"\b(my )?(stalker|abuser|attacker)\b",
            r"\b(intruder|stranger|someone|somebody) (is |was |got |broke )?"
            r"(in|into|inside|breaking into) (my|our|the) (room|dorm|apartment|house|townhouse)\b|"
            r"\bbroke into my\b",
        ),
        "fire": (
            r"\b(on fire|caught fire|flames|smoke (everywhere|coming|pouring|filling)|gas leak)\b",
            rf"\bfire in (the|my|our)\b{_NOT_A_HAZARD}|\bcarbon monoxide\b",
            rf"\b(there is|theres|i see|i smell|we smell) (a )?(fire|smoke|gas)\b{_NOT_A_HAZARD}",
            r"\bsmell (smoke|gas)\b",
        ),
        # Taking too much could be an accident or on purpose, and a spiked drink is both a
        # danger and a medical problem, so these get the general text.
        "other": (
            r"\boverdos",
            r"\b(took|taken|swallowed|popped|downed) (a |an |the |my )?"
            r"(whole bottle|bottle of|handful of|too many|too much|a lot of)\b",
            r"\b(spiked|drugged|roofied)\b|\bput something in my (drink|water|cup)\b",
            r"\b(911|ambulance)\b",
            r"\b(this is|its|it is|there is|theres) an emergency\b",
            r"\b(im|i am|we are|were|he is|hes|she is|shes|they are) in "
            r"(immediate |serious |real )?danger\b",
        ),
    }.items()
}


def plain(text: str) -> str:
    """Lower case, no accents, apostrophes or invisible characters, punctuation as spaces."""
    text = re.sub(r"['`´ʹʻʼˈ’‘′‛＇]", "", text)
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(c for c in text if unicodedata.category(c) not in ("Mn", "Cf"))
    return re.sub(r"[\W_]+", " ", text).strip()


def check(turn: Turn) -> BoundaryResult:
    """Read the Turn's message. Only the latest message counts, not earlier ones."""
    text = plain(turn.message)
    kinds = {name for name, patterns in _DANGER.items() if any(p.search(text) for p in patterns)}
    if not kinds:
        return CONTINUE
    situation = kinds.pop() if len(kinds) == 1 else "other"
    return BoundaryResult("safety", safety_text(situation), situation)
