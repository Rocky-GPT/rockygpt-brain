from __future__ import annotations

import re

from rockygpt_brain.brain.understand.schema import Understanding

_MATCHES_EVERYTHING = 3


class ResolutionFailed(Exception):
    pass


def inconsistent(read: Understanding) -> bool:
    """BRAIN #1 contradicting itself: context was needed, and nothing shows it.

    `usesContext` with no references and a resolution identical to the
    normalized question proves neither reading. A self-contained question asked
    mid-thread lands here — there was nothing to resolve, and coming back
    unchanged is the correct answer. So does a real follow-up whose referent
    BRAIN #1 never found, where continuing plans a question nobody asked.

    Refusing the state outright made the first case a stochastic 503: on a
    measured ten reads of one self-contained question with a conversation
    present, `usesContext` was true every time, and the turn survived only
    because the resolution happened to differ. Passing it instead would let the
    second case through, which is the failure the guard exists to prevent.

    So it is neither, on one reading. `brain` reads the question once more and
    refuses if the second reading says the same thing — the states that *are*
    decisive stay with `unresolved`, where a reference named and left
    unresolved is a failure however many times it is read.
    """
    return (
        read.uses_context
        and not read.references
        and read.resolved.strip().casefold() == read.normalized.strip().casefold()
    )


def unresolved(read: Understanding) -> str:
    if not read.uses_context:
        return ""

    if read.references and read.resolved.strip().casefold() == read.normalized.strip().casefold():
        return "the question needed the conversation and came back unchanged"

    for reference in read.references:
        substantial = [
            w for w in re.findall(r"[\w'-]+", reference.refers_to) if len(w) > _MATCHES_EVERYTHING
        ]
        if substantial:
            if not any(
                re.search(rf"\b{re.escape(w)}", read.resolved, re.IGNORECASE) for w in substantial
            ):
                return f"nothing of {reference.refers_to!r} reached the question"
            continue
        pointing_word = reference.text.strip()
        if pointing_word and re.search(
            rf"\b{re.escape(pointing_word)}\b", read.resolved, re.IGNORECASE
        ):
            return f"{pointing_word!r} still stands unresolved in the question"
    return ""
