"""Is this reading fit to plan from?

BRAIN #2 is shown `resolved` and nothing else — not the conversation, not the
words as typed. That is the design, and it holds only while `resolved` really
does stand on its own. When it does not, nothing downstream can notice: the
planner reads a question that is merely vague rather than visibly broken, plans
something plausible for it, and the turn returns a confident answer to a
question nobody asked.

So the check happens here, at the seam, rather than after three more stages
have built on it.
"""

from __future__ import annotations

import re

from rockygpt_brain.brain.understand.schema import Understanding


class ResolutionFailed(Exception):
    """Why a reading could not be planned from. The cause of the ServiceError."""


def unresolved(read: Understanding) -> str:
    """Why this reading cannot be planned from, or empty when it can.

    BRAIN #2 is shown `resolved` and nothing else — not the conversation, not
    the words as typed. That is the design, and it holds only while `resolved`
    really does stand on its own. When it does not, nothing downstream can
    notice: the planner reads a question that is merely vague rather than
    visibly broken, plans something plausible for it, and the turn comes back
    an answer to a question nobody asked.

    So a resolution is checked here, at the seam, rather than after three more
    stages have built on it. Both tests are properties of what BRAIN #1 said
    about its own work — no phrase list, and no judgement about the subject.
    """
    if not read.uses_context:
        return ""

    # It said the question needed the conversation, then wrote back the same
    # sentence. Whatever it borrowed, none of it arrived.
    if read.resolved.strip().casefold() == read.normalized.strip().casefold():
        return "the question needed the conversation and came back unchanged"

    for reference in read.references:
        # A reference is resolved when what it points at reached the question.
        # Whether the pointing word also survived is not the test: BRAIN #1
        # regularly keeps it and appends the referent — "tomorrow" becomes
        # "tomorrow, 2026-08-26" — and rejecting that cost one good resolution
        # in eight when measured.
        #
        # Any one substantial word of the referent counts rather than the
        # phrase entire, because a referent is often reworded on the way in.
        # Short words are skipped: they match everything.
        parts = [w for w in re.findall(r"[\w'-]+", reference.refers_to) if len(w) > 3]
        if parts:
            if not any(
                re.search(rf"\b{re.escape(w)}", read.resolved, re.IGNORECASE) for w in parts
            ):
                return f"nothing of {reference.refers_to!r} reached the question"
            continue
        # A referent of nothing but short words cannot be judged that way, so
        # fall back to the weaker signal: the pointing word standing alone.
        word = reference.text.strip()
        if word and re.search(rf"\b{re.escape(word)}\b", read.resolved, re.IGNORECASE):
            return f"{word!r} still stands unresolved in the question"
    return ""
