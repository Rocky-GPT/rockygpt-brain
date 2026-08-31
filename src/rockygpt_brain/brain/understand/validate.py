from __future__ import annotations

import re

from rockygpt_brain.brain.plan.schema import TIME_WORDS
from rockygpt_brain.brain.understand.schema import Reading


class ResolutionFailed(Exception):
    pass


_WORD = re.compile(r"[\w'-]+")

# Words the clock answers, so no conversation can be needed to read them: the
# planner's own list, plus the times of day that resolve to a date the same way.
_THE_CLOCK_ANSWERS = frozenset(TIME_WORDS) | {
    "tonight",
    "today's",
    "tonight's",
    "morning",
    "afternoon",
    "evening",
    "night",
    "this",
}


def narrowed(reading: Reading) -> Reading:
    """Drops the spans that were never gaps, and re-derives what is left.

    A span made only of clock words is not something the conversation holds:
    the hour is known without asking anyone. Sending it to be resolved is how a
    self-contained question reaches history at all, and history fills it with
    whatever was last said — "tonight" came back as an earlier answer about
    dinner, and a question about events stopped being about events.

    Dropped rather than refused, the direction `selective` gives for a limit of
    one: the question is good and answerable, and the only thing wrong with the
    reading is that it reached somewhere it did not need to. What survives
    decides `needsContext`, so a reading with nothing left is frozen exactly as
    a reading that never reached at all.
    """
    kept = [
        span
        for span in reading.unresolved
        if not (
            (words := {word.casefold() for word in _WORD.findall(span.text)})
            and words <= _THE_CLOCK_ANSWERS
        )
    ]
    if len(kept) == len(reading.unresolved):
        return reading
    return reading.model_copy(update={"unresolved": kept, "needs_context": bool(kept)})


def incoherent(reading: Reading) -> str:
    """Whether the reading contradicts itself about needing the conversation.

    `needsContext` and `unresolvedReferences` are one fact stated twice, so
    disagreement is not a judgement to interpret — it is a reading that cannot
    be acted on either way. Claiming the conversation while naming nothing
    leaves the second reading with nothing to fill; naming a span while
    claiming the question stands alone leaves the span unfilled, and a question
    still full of pointing words reaches the planner, which answers it
    confidently against whatever it happens to match.

    A span also has to be words the question contains. One that is not is text
    the second reading is asked to find and cannot, so it substitutes where it
    likes — and what it substitutes into is the whole sentence.
    """
    if reading.needs_context and not reading.unresolved:
        return "the reading needed the conversation and named nothing in the question to fill"
    if reading.unresolved and not reading.needs_context:
        spans = ", ".join(repr(span.text) for span in reading.unresolved)
        return f"the reading left {spans} unresolved while saying the question stands alone"

    asked = reading.normalized.casefold()
    for span in reading.unresolved:
        if span.text.strip().casefold() not in asked:
            return f"{span.text!r} is not a span of the question"
    return ""
