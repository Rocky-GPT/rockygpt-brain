"""What running a lane produced.

Three shapes, and which one it is says what happened. The distinction that
matters is `{"results": []}` against `{"note": ...}`: "Rocky looked and there
is nothing" against "Rocky never looked". Those are different answers, and the
empty list is what says which — never drop it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import ceil
from typing import Any

OWN_KNOWLEDGE = "ownKnowledge"
CAMPUS_DATA = "campusData"
WEB = "web"
DOCUMENTS = "documents"
SAFETY = "safety"
RAG_DISABLED = "ragDisabled"

#: What Rocky says when the documents were searched and do not answer the
#: question. Python's words, not the model's: a model that has just judged its
#: evidence insufficient is the last thing that should be asked to phrase the
#: admission, and this is the sentence that must not drift into a hedged
#: half-answer.
INSUFFICIENT_EVIDENCE = (
    "Ramapo's documents do not cover that, so I cannot answer it from them. "
    "The office that owns the subject will have it."
)


class Mode(StrEnum):
    """How much to write about each row."""

    DETAILED = "detailed"  # few enough to describe one by one
    COMPACT = "compact"  # a line each
    PAGINATED = "paginated"  # a line each, and only the first page of them


#: How many rows can be described one at a time before a list stops being one,
#: and how many can be listed at all before it has to be paged. `PAGE` is what
#: a page holds past that.
DETAILED_UP_TO = 10
COMPACT_UP_TO = 50
PAGE = 25


@dataclass(frozen=True, slots=True)
class Presentation:
    """How much of a result can be shown at once, and in what shape.

    Python's decision, and the reason it is Python's is that BRAIN #3 is bad at
    it. Handed a hundred rows it has judged the number itself twice, and been
    wrong both times: once writing "I cannot provide a complete list" over a
    result that was complete, once quietly describing the first few and
    stopping without saying it had. Neither is visible in the answer.

    So the count decides, here, before anything is written. `mode` is handed to
    BRAIN #3 as an instruction about detail; the page arithmetic is for whoever
    renders the result, which is not the model.
    """

    mode: Mode
    page_size: int
    page: int = 1
    total_pages: int = 1

    def summary(self) -> dict[str, Any]:
        """For a person reading the trace, and for the client rendering pages.

        No `pageSize`: it is `showing` beside this, and one number written
        twice is one number that can disagree with itself.
        """
        return {"mode": self.mode.value, "page": self.page, "totalPages": self.total_pages}


def present(rows: int) -> Presentation:
    """How much of a result of this size to show at once.

    A ladder and nothing else. No capability is consulted, no phrasing is read,
    and the count is the only input — the same number of rows always presents
    the same way, which is what makes this reviewable at all.

    The planner was briefly asked one thing the count cannot answer: whether
    the question wanted the whole result in one message rather than a page at
    a time. It measured badly on both sides. The field came back set on "when
    is the next shuttle" and unset on "show me 100 courses", and the sentence
    describing it cost a question it had nothing to do with — "where is the
    Anisfield School of Business" went from planning cleanly to being rejected
    three times in five. Asking about presentation at all is what did the
    damage; the count is here precisely so nothing has to.
    """
    if rows <= DETAILED_UP_TO:
        return Presentation(Mode.DETAILED, rows)
    if rows <= COMPACT_UP_TO:
        return Presentation(Mode.COMPACT, rows)
    return Presentation(Mode.PAGINATED, PAGE, total_pages=ceil(rows / PAGE))


@dataclass(frozen=True, slots=True)
class Execution:
    #: No lane here. The plan stage above already names it, and repeating it
    #: only invites the two to disagree. What this stage adds is where the
    #: answer comes from, and what came back.
    answer_from: str
    note: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    count: int | None = None
    #: What the lookup asked for. Only sent on when it came back with nothing,
    #: because an empty list on its own cannot be read: "there are none" is
    #: unanswerable without none *of what*. With rows, they say it themselves.
    looked_for: dict[str, Any] = field(default_factory=dict)
    #: How many rows the result set holds, set only when more were found than
    #: were handed on. What is handed on is one page; this is what it is a page
    #: of, and the difference has to survive as far as whoever writes the
    #: sentence. Without it a page reads as the whole of it.
    found: int | None = None
    #: How much of the result to show, and in what shape. Set by the lane that
    #: can return more rows than a message holds, which is the CODE lane and
    #: only it — a web search comes back with a handful and a document search
    #: with a few passages, and telling BRAIN #3 how to lay out five things is
    #: an instruction it does not need.
    shown: Presentation | None = None

    @property
    def ran(self) -> bool:
        """Whether this stage produced anything to answer from. An outage did not.

        `safety` counts. Nothing was looked up, but the resources are the
        content of the answer, and they have to reach BRAIN #3 the same way
        rows do.
        """
        return self.answer_from in (CAMPUS_DATA, WEB, DOCUMENTS, SAFETY)

    def summary(self) -> dict[str, Any]:
        """This stage, for a person reading the trace.

        ``answerFrom`` leads, and is the same value BRAIN #3 was handed — so
        the handoff is visible rather than something you have to take on
        trust. What follows it is what BRAIN #3 got, plus, when nothing ran, a
        ``note`` saying why. The note is the one thing here BRAIN #3 never
        sees; telling it a lookup failed makes it apologise for the capability
        instead of answering.

        ``{"answerFrom": "ownKnowledge", "note": ...}``  nothing was looked up
        ``{"answerFrom": "campusData", "count": n}``     it ran and counted
        ``{"answerFrom": "campusData", "results": []}``  it ran and matched none

        A result too large to hand to a model carries ``showing``, ``outOf``
        and ``presentation`` as well, so a page never reads as the whole of it.

        Do not drop ``results`` when it is empty. "Rocky looked and there is
        nothing" and "Rocky never looked" are different answers, and the empty
        list is what says which.
        """
        if not self.ran:
            return {"answerFrom": self.answer_from, "note": self.note}
        if self.count is not None:
            return {"answerFrom": self.answer_from, "count": self.count}
        summarised: dict[str, Any] = {"answerFrom": self.answer_from}
        if self.results:
            summarised["showing"] = len(self.results)
        if self.found is not None:
            summarised["outOf"] = self.found
        # Nothing to lay out, nothing to say about laying it out. An empty
        # result is already an answer on its own terms.
        if self.shown is not None and self.results:
            summarised["presentation"] = self.shown.summary()
        summarised["results"] = self.results
        return summarised

    def grounding(self) -> dict[str, Any]:
        """What BRAIN #3 answers from. Every lane produces one; none is empty.

        ``answerFrom`` is an instruction, never a status. It says where this
        answer comes from — not that anything is missing, broken, or not built
        yet. A lane with no executor is indistinguishable here from a question
        that never needed one, and that is the point: told a lookup failed,
        BRAIN #3 apologises for a capability instead of answering the question.

        ``campusData`` rides along only when there was a lookup. Empty means it
        ran and matched nothing, which is an answer in itself — and now says so
        unambiguously, because ``answerFrom`` already established that it ran.
        """
        if not self.ran:
            return {"answerFrom": self.answer_from}
        found = [{"count": self.count}] if self.count is not None else self.results
        grounded: dict[str, Any] = {"answerFrom": self.answer_from, "results": found}
        if self.shown is not None and found:
            # How much to write about each row — decided from how many there
            # are, before anything was written, because left to judge the
            # number itself BRAIN #3 has got it wrong in both directions.
            grounded["presentation"] = self.shown.mode.value
        if found:
            # How many rows are here, stated rather than left to be counted.
            # Handed a hundred courses and asked for a hundred, a model counted
            # them, got it wrong, and answered "the data does not reach that
            # number" while holding exactly that number.
            grounded["showing"] = len(found)
        if self.found is not None:
            # And how many there are. Without it a model reads its page as
            # everything there is and writes "Ramapo offers these courses".
            grounded["outOf"] = self.found
        if not found and self.looked_for:
            # The one case where the rows cannot speak for themselves. Without
            # this the honest answer — "there are none left today" — is not
            # available, and what comes back instead is "no information", which
            # says Rocky does not know rather than that there are none.
            grounded["lookedFor"] = self.looked_for
        return grounded
