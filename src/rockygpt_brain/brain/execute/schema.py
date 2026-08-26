"""What running a lane produced.

Three shapes, and which one it is says what happened. The distinction that
matters is `{"results": []}` against `{"note": ...}`: "Rocky looked and there
is nothing" against "Rocky never looked". Those are different answers, and the
empty list is what says which — never drop it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OWN_KNOWLEDGE = "ownKnowledge"
CAMPUS_DATA = "campusData"
WEB = "web"
DOCUMENTS = "documents"
SAFETY = "safety"

#: What Rocky says when the documents were searched and do not answer the
#: question. Python's words, not the model's: a model that has just judged its
#: evidence insufficient is the last thing that should be asked to phrase the
#: admission, and this is the sentence that must not drift into a hedged
#: half-answer.
INSUFFICIENT_EVIDENCE = (
    "Ramapo's documents do not cover that, so I cannot answer it from them. "
    "The office that owns the subject will have it."
)


@dataclass(frozen=True, slots=True)
class Execution:
    #: No lane here. The plan stage above already names it, and repeating it
    #: only invites the two to disagree. What this stage adds is where the
    #: answer comes from, and what came back.
    answer_from: str
    note: str = ""
    results: list[dict[str, Any]] = field(default_factory=list)
    count: int | None = None

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

        Do not drop ``results`` when it is empty. "Rocky looked and there is
        nothing" and "Rocky never looked" are different answers, and the empty
        list is what says which.
        """
        if not self.ran:
            return {"answerFrom": self.answer_from, "note": self.note}
        if self.count is not None:
            return {"answerFrom": self.answer_from, "count": self.count}
        return {"answerFrom": self.answer_from, "results": self.results}

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
        return {"answerFrom": self.answer_from, "results": found}
