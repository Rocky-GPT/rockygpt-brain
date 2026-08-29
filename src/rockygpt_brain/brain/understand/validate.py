from __future__ import annotations

import re

from rockygpt_brain.brain.understand.schema import Understanding

_MATCHES_EVERYTHING = 3


class ResolutionFailed(Exception):
    pass


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
