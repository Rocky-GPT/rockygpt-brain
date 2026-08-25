"""PYTHON: run the lane.

The stage between the two brains. A checked plan goes in; what the lane
produced comes out, and that is what BRAIN #3 writes the answer from.

Two jobs live here and nowhere else:

Translation. The plan is written in Rocky's own vocabulary — `date`,
`destination`, `orderBy` — and the data service has its own names for those.
Turning one into the other is this file's work, which is what keeps the
vocabulary free to stay small and generic.

The generic operations. `orderBy`, `limit`, `count` are applied here, in
Python, over whatever the lookup returned. The data service has its own
selection vocabulary; asking it for everything and sorting the result ourselves
means none of that vocabulary has to leak back into a plan.

A capability earns its executor with an entry in `_EXECUTORS`. Nothing else
moves.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rockygpt_brain.capabilities.registry import CAPABILITIES
from rockygpt_brain.core.plan import Lane, Operation, Plan
from rockygpt_brain.errors import ServiceError
from rockygpt_brain.safety.enforce import required
from rockygpt_brain.services.data import DataPort, DataUnavailable
from rockygpt_brain.services.web import WebPort, WebUnavailable

#: The most a lookup asks for before the operation narrows it down.
_FETCH_LIMIT = 100
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})\s*([AaPp])[Mm]?$")


#: Where an answer comes from. `campusData` and `web` are looked up and carry
#: `results`; the other two do not.
#:
#: There is no value here for a lookup that failed. This stage either produces
#: what BRAIN #3 writes from, or it raises — no stage compensates for the one
#: before it.
OWN_KNOWLEDGE = "ownKnowledge"
CAMPUS_DATA = "campusData"
WEB = "web"
SAFETY = "safety"


class LaneFailed(Exception):
    """Why a lane could not run. Carried as the cause of the ServiceError."""


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
        return self.answer_from in (CAMPUS_DATA, WEB, SAFETY)

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


Executor = Callable[[Plan, datetime, DataPort], Awaitable[list[dict[str, Any]]]]


def _project(record: dict[str, Any], capability: str) -> dict[str, Any]:
    """One record, cut down to the fields the capability publishes."""
    entry = CAPABILITIES[capability]
    return {name: read(record) for name, read in entry.read.items() if name in entry.fields}


def _apply(
    records: list[dict[str, Any]], operation: Operation, capability: str
) -> tuple[list[dict[str, Any]], int | None]:
    """`orderBy`, `limit` and `count`, over whatever the lookup returned."""
    rows = list(records)
    entry = CAPABILITIES[capability]
    if operation.order_by:
        key = entry.sort.get(operation.order_by) or entry.read.get(operation.order_by)
        if key is not None:
            rows.sort(key=key, reverse=operation.direction == "descending")
    if operation.count:
        # Counted before the limit: the answer is how many matched, not how
        # many were kept.
        return [], len(rows)
    if operation.limit is not None:
        rows = rows[: operation.limit]
    return [_project(row, capability) for row in rows], None


async def run(checked: Plan, now: datetime, data: DataPort, web: WebPort) -> Execution:
    """Act on a checked plan, or fail the turn.

    Nothing here degrades. A lane that cannot run raises rather than handing
    BRAIN #3 something to write around — the alternative is an answer invented
    to cover a lookup that never happened, which reads exactly like one that
    did.
    """
    # Before the lane, and instead of it. Every concern the plan raises is
    # acted on, not the first — a question can ask for a password on behalf of
    # someone in trouble, and both halves need answering. This depends on
    # nothing that can fail: no capability, no executor, no network. That is
    # the point of doing it here, since the turns that most need an answer are
    # the ones least able to wait for campus data to come back.
    if checked.safety:
        return Execution(SAFETY, results=required(checked.safety))

    if checked.lane is Lane.GENERAL:
        return await _general(checked, web)

    capability = checked.capability or ""
    entry = CAPABILITIES.get(capability) if checked.lane is Lane.CODE else None
    if entry is None:
        missing = f"the {capability} capability" if capability else f"the {checked.lane.value} lane"
        raise ServiceError(
            503, "SERVICE_UNAVAILABLE", "Rocky cannot look that up yet.", retryable=False
        ) from LaneFailed(f"no executor for {missing}")

    try:
        # The filters, not the plan. A capability has no business knowing
        # what a lane is or which operations exist.
        records = await entry.execute(checked.filter_values, now, data)
    except DataUnavailable as exc:
        raise ServiceError(
            503,
            "DATASET_UNAVAILABLE",
            "Rocky could not reach campus data just now.",
            retryable=True,
        ) from exc

    results, count = _apply(records, checked.operation, capability)
    return Execution(CAMPUS_DATA, results=results, count=count)


async def _general(plan: Plan, web: WebPort) -> Execution:
    """General knowledge: the model's own, unless the answer has a shelf life.

    A `stable` question needs no lookup, so producing nothing is this stage
    succeeding rather than failing. A `current` one does need one, and a search
    that does not answer fails the turn like any other.
    """
    if plan.freshness != "current" or not plan.query:
        return Execution(OWN_KNOWLEDGE, note="stable; answered from what the model knows")
    # What `validate` dated, never the planner's own wording — the anchoring is
    # the point of having two fields, and falling back to `query` here would
    # quietly undo it on any path that forgot to set the other.
    try:
        results = await web.search(plan.effective_query or plan.query)
    except WebUnavailable as exc:
        raise ServiceError(
            503, "SERVICE_UNAVAILABLE", "Rocky could not look that up just now.", retryable=True
        ) from exc
    return Execution(WEB, results=results)
