"""Act on a checked plan, or fail the turn.

Nothing here degrades. A lane that cannot run raises rather than handing
BRAIN #3 something to write around — the alternative is an answer invented to
cover a lookup that never happened, which reads exactly like one that did.
"""

from __future__ import annotations

from datetime import datetime

from rockygpt_brain.brain.execute.schema import SAFETY, Execution
from rockygpt_brain.brain.plan.schema import Lane, Plan
from rockygpt_brain.errors import ServiceError
from rockygpt_brain.lanes import code, general
from rockygpt_brain.safety.enforce import required
from rockygpt_brain.services.data import DataPort
from rockygpt_brain.services.web import WebPort


async def run(checked: Plan, now: datetime, data: DataPort, web: WebPort) -> Execution:
    # Before the lane, and instead of it. Every concern the plan raises is
    # acted on, not the first — a question can ask for a password on behalf of
    # someone in trouble, and both halves need answering. This depends on
    # nothing that can fail: no capability, no executor, no network. That is
    # the point of doing it here, since the turns that most need an answer are
    # the ones least able to wait for campus data to come back.
    if checked.safety:
        return Execution(SAFETY, results=required(checked.safety))

    if checked.lane is Lane.GENERAL:
        return await general.run.run(checked, web)
    if checked.lane is Lane.CODE:
        return await code.run.run(checked, now, data)

    # RAG has no code yet. It says so here rather than returning an empty
    # result, which BRAIN #3 would read as "looked, found nothing".
    raise ServiceError(
        503, "SERVICE_UNAVAILABLE", "Rocky cannot look that up yet.", retryable=False
    ) from code.run.LaneFailed(f"no code for the {checked.lane.value} lane")
