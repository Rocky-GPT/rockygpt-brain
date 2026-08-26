from __future__ import annotations

from rockygpt_brain.brain.execute.schema import DOCUMENTS, Execution
from rockygpt_brain.brain.plan.schema import Plan
from rockygpt_brain.errors import Unavailable
from rockygpt_brain.services.rag.client import RagPort, RagUnavailable

PASSAGES = 5


async def run(plan: Plan, rag: RagPort) -> Execution:
    topic = plan.topic or ""
    try:
        passages = await rag.retrieve(topic, PASSAGES)
    except RagUnavailable as exc:
        raise Unavailable("Rocky could not search the campus documents just now.") from exc
    return Execution(
        DOCUMENTS,
        results=[
            {
                "passage": passage.content,
                "domain": passage.domain,
                "title": passage.title,
                "url": passage.url,
            }
            for passage in passages
        ],
    )
