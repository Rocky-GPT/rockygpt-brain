from __future__ import annotations

from rockygpt_brain.brain.execute.schema import OWN_KNOWLEDGE, WEB, Execution
from rockygpt_brain.brain.plan.schema import Plan
from rockygpt_brain.errors import Unavailable
from rockygpt_brain.services.web import WebPort, WebUnavailable


async def run(plan: Plan, web: WebPort) -> Execution:
    if plan.freshness != "current" or not plan.query:
        return Execution(OWN_KNOWLEDGE, note="stable; answered from what the model knows")
    try:
        results = await web.search(plan.effective_query or plan.query)
    except WebUnavailable as exc:
        raise Unavailable("Rocky could not look that up just now.") from exc
    return Execution(WEB, results=results)
