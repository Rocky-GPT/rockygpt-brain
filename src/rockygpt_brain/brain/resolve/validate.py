from __future__ import annotations

import re
from typing import Any

from rockygpt_brain.brain.resolve.schema import Resolution

# Words this short match everything and mean nothing on their own.
_MATCHES_EVERYTHING = 3
_WORD = re.compile(r"[\w'-]+")

# Words that ask the question rather than narrow it. A resolution is free to
# lose or gain these — "what about the one after that" becoming "when does the
# shuttle after 11:00 AM depart" keeps every constraint and almost none of the
# phrasing. Only what a question *names* is held to, which is what makes this a
# check on meaning rather than on wording.
_ASKS_RATHER_THAN_NARROWS = frozenset(
    {
        "what",
        "when",
        "where",
        "which",
        "whom",
        "whose",
        "there",
        "that",
        "this",
        "these",
        "those",
        "they",
        "them",
        "then",
        "about",
        "does",
        "did",
        "done",
        "will",
        "would",
        "could",
        "should",
        "have",
        "has",
        "had",
        "been",
        "being",
        "your",
        "yours",
        "tell",
        "show",
        "give",
        "know",
        "please",
        "there's",
        "it's",
        "many",
        "much",
        "some",
        "any",
        "just",
        "also",
        "still",
        "than",
        "from",
        "with",
        "into",
        "over",
    }
)


def _content(text: str) -> set[str]:
    return {
        word.casefold()
        for word in _WORD.findall(text)
        if len(word) > _MATCHES_EVERYTHING and word.casefold() not in _ASKS_RATHER_THAN_NARROWS
    }


def _said(earlier: list[dict[str, Any]]) -> set[str]:
    return _content(" ".join(str(value) for turn in earlier for value in turn.values()))


def contaminated(
    question: str, spans: list[str], resolution: Resolution, earlier: list[dict[str, Any]]
) -> str:
    """Whether the conversation did more to the question than fill its gaps.

    The invariant: a turn that could be read on its own keeps its meaning, and
    a turn that could not gets exactly its gaps filled. History resolves
    ambiguity; it never creates intent.

    Two ways that breaks, and both have been seen. Something the question
    stated goes missing, because the resolution rewrote around it. Or something
    only an earlier turn named arrives in the question — asked about breakfast
    and dinner after a turn about breakfast and lunch, the resolution came back
    naming all three, and the lookup that followed answered a question nobody
    asked.

    Checked on content words rather than spans, because the resolution is
    allowed to reword: what it may not do is lose what was stated or import
    what was not. Words inside the spans it was told to fill are exempt in both
    directions — that is the work.
    """
    inside_spans = _content(" ".join(spans))
    stated = _content(question) - inside_spans
    dropped = stated - _content(resolution.resolved)
    if dropped:
        return f"the resolution dropped {', '.join(sorted(dropped))} from the question"

    filled = _content(" ".join(reference.refers_to for reference in resolution.references))
    added = _content(resolution.resolved) - _content(question) - filled
    borrowed = added & _said(earlier)
    if borrowed:
        return f"the conversation added {', '.join(sorted(borrowed))} to the question"
    return ""
