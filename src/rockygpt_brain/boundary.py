"""Step 2, safety and capability boundaries: the first look at every Turn.

Code alone reads the message and code alone writes every reply here. No model is involved, so
the same message always takes the same path:

- `safety`: someone may be in immediate danger. Danger wins over everything else.
- `capability_limit`: the student's own account, or something RockyGPT would have to do for
  them. RockyGPT can't see or change either.
- `continue`: anything else goes on to the next step.

Deciding by phrases is blunt on purpose. A wrong `safety` shows 911 help nobody needed; a wrong
`continue` could miss an emergency. A wrong `capability_limit` sends the student to the office
that has their account, so those phrases are the narrowest.
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

# A hazard word followed by one of these is a question about a program or a rule, not a hazard.
_NOT_A_HAZARD = (
    r"(?! (?:\w+ )?(?:drills?|training|policy|policies|protocol|procedures?|plans?|prevention|"
    r"awareness|safety|clubs?|workshops?|classes|class|courses?|programs?|rules?|alarms?|"
    r"extinguishers?|exits?|inspections?|evacuations?|regulations?|detectors?|meetings?))"
)
_SELF = r"(my ?self|him ?self|her ?self|them ?selves|themselves)"
_DANGER = [re.compile(p) for p in (
    # hurting oneself
    rf"\b(kill|hurt|harm|cut|injur|stab|shoot|hang)\w* {_SELF}\b",
    r"\b(end|take) my (own )?life\b",
    rf"\bsuicid\w*\b{_NOT_A_HAZARD}",
    r"\b(want|wanna|going|plan|planning) to die\b",
    r"\b(dont|do not) (want to|wanna) (live|be alive|exist|be here|go on|keep going)",
    r"\b(end it all|better off dead|not worth living|no reason to live)\b",
    r"\bjump(ing)? (off|from) (the |a |my )?(roof|bridge|building|garage|parking garage|balcony)\b",
    # taking too much of something
    r"\boverdos",
    r"\b(took|taken|swallowed|popped|downed) (a |an |the |my |some |too |way |whole |bunch |"
    r"handful |lot )*(bottle|pills?|bunch|handful|something|meds|medication|much|many)\b",
    r"\b(spiked|drugged|roofied)\b|\bput something in my (drink|water|cup)\b",
    # a body in trouble
    r"\b(not|isnt|arent|stopped|stop|cant|cannot|wont|hardly|barely) breath(e|ing)\b",
    r"\b(trouble|difficulty) breathing\b|\bunable to breathe\b",
    r"\b(wont|cant|couldnt|cannot|not|isnt|barely) (wake|waking|respond|responding|moving|"
    r"responsive|conscious)\b",
    r"\b(unconscious|unresponsive|passed out|blacked out|collapsed|fainted|choking|"
    r"heart attack|having a stroke|chest pain)\b",
    rf"\bseizure{_NOT_A_HAZARD}",
    r"\b(turning|turned|lips are) blue\b",
    r"\bbleeding (from|out|badly|a lot|heavily|profusely|through)\b|"
    r"\b(cant|cannot|wont|will not) stop bleeding\b|\bblood (everywhere|all over|pouring)\b",
    r"\b(911|ambulance)\b",
    # someone or something dangerous right now
    rf"\b(active shooter|shooter|gunman|bomb threat){_NOT_A_HAZARD}",
    r"\b(gunshots?|shots fired|hostage)\b",
    r"\bshoot(ing)? up\b|\bthreaten\w* to (shoot|kill|stab|bomb|hurt|attack)\b",
    r"\b(has|have|with|holding|pulled|pointed) (a |an )?(gun|knife|weapon)\b",
    r"\b(gonna|going to|will|wants to|want to) (hurt|kill|hit|beat|attack|stab|shoot|rape) "
    r"(me|us|him|her|them|people|everyone)\b",
    r"\b(being|getting|got|been|was) (just )?(attacked|assaulted|robbed|stalked|followed|chased|"
    r"threatened|jumped|mugged|beaten|kidnapped|stabbed|shot|choked)\b",
    r"\b(attacking|hurting|threatening|stalking|following|chasing|shooting|hitting|beating|"
    r"hit|punched|slapped|choked|grabbed|dragged|pinned|trapped|locked|cornered|kidnapped) me\b",
    r"\b(was|been|got)( just| recently| again)? (raped|sexually assaulted|molested)\b",
    r"\b(my )?(stalker|abuser|attacker)\b",
    r"\b(intruder|stranger|someone|somebody) (is |was |got |broke )?(in|into|inside|breaking into) "
    r"(my|our|the) (room|dorm|apartment|house|townhouse)\b|\bbroke into my\b",
    # fire, smoke, gas
    r"\b(on fire|caught fire|flames|smoke (everywhere|coming|pouring|filling)|gas leak)\b",
    rf"\bfire in (the|my|our)\b{_NOT_A_HAZARD}|\bcarbon monoxide\b",
    rf"\b(there is|theres|i see|i smell|we smell) (a )?(fire|smoke|gas)\b{_NOT_A_HAZARD}",
    r"\bsmell (smoke|gas)\b",
    r"\b(this is|its|it is|there is|theres) an emergency\b",
    r"\b(im|i am|we are|were|he is|hes|she is|shes|they are) in (immediate |serious |real )?"
    r"danger\b",
)]

_HOW_TO = re.compile(
    r"^(?:(?:hey|hi|hello|please) )*(?:how (?:do|can|could|would|should|does|to)\b|"
    r"where (?:do|can|could|would|to)\b|who (?:do|can|should|would) i\b|"
    r"(?:which|what) (?:office|department)\b)"
)
_MINE = r"my (?:(?:current|final|midterm|overall|student|account|total|exam|test|class|semester|"
_MINE += r"unofficial|official|remaining|outstanding|tuition|financial|meal|dining|parking|"
_MINE += r"housing|room|next) )*"
_OWN_ACCOUNT = [re.compile(p) for p in (
    _MINE + r"(grades?|gpa|transcripts?|schedule|classes|courses|balance|bills?|tuition|"
    r"financial aid|aid|scholarships?|loans?|refund|holds?|degree audit|registration|account|"
    r"password|login|student id|id number|credits|application|admission status|"
    r"enrollment status|room assignment|housing assignment|advisors?|permit|swipes|"
    r"dollars)\b",
    r"\bmeal plan (balance|swipes)\b",
    r"\bhow (many credits|much) (do|have|did) i (have|owe|complete\w*|earn\w*)\b|"
    r"\bhow many credits have i\b|\bdo i owe\b",
    r"\b(financial aid|aid|scholarship|loans?|refund) did i (get|receive)\b",
    r"\bam i (registered|enrolled|failing|passing|on (academic )?probation|on the deans list)\b",
    r"\bdid i (pass|fail|get accepted|get in)\b",
    r"\bdo i have (a |any )?holds?\b",
    r"\bhave i (paid|registered)\b",
    r"(?:^|\b(?:also|and|then|plus) )(?:(?:hey|hi|hello|please|pls|ok|okay|so) )*"
    r"(?:(?:can|could|would|will) you (?:please )?|i (?:need|want) you to |id like you to )?"
    r"(?:register|enroll|unenroll|sign|drop|withdraw|waitlist|add|remove|pay|apply|"
    r"submit|cancel|reset|unlock|change|update|book|reserve|schedule|email|send|order|request|"
    r"file|transfer|switch|swap|check in|declare|waive|renew)\b.{0,25}?\b(me|my)\b",
)]


def plain(text: str) -> str:
    """Lower case, no accents, apostrophes or invisible characters, punctuation as spaces."""
    text = re.sub(r"['`´ʹʻʼˈ’‘′‛＇]", "", text)
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(c for c in text if unicodedata.category(c) not in ("Mn", "Cf"))
    return re.sub(r"[\W_]+", " ", text).strip()


def check(turn: Turn) -> BoundaryResult:
    """Read the Turn's message. Only the latest message counts, not earlier ones."""
    text = plain(turn.message)
    if any(pattern.search(text) for pattern in _DANGER):
        return BoundaryResult("safety", SAFETY_MESSAGE)
    if not _HOW_TO.match(text) and any(pattern.search(text) for pattern in _OWN_ACCOUNT):
        return BoundaryResult("capability_limit", CAPABILITY_MESSAGE)
    return CONTINUE
