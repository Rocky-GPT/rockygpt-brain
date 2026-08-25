"""General knowledge: the model's own, unless the answer has a shelf life."""

from __future__ import annotations

from rockygpt_brain.brain.execute.schema import OWN_KNOWLEDGE, WEB, Execution
from rockygpt_brain.brain.plan.schema import Plan
from rockygpt_brain.errors import Unavailable
from rockygpt_brain.services.web import WebPort, WebUnavailable


async def run(plan: Plan, web: WebPort) -> Execution:
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
        raise Unavailable("Rocky could not look that up just now.") from exc
    return Execution(WEB, results=results)
