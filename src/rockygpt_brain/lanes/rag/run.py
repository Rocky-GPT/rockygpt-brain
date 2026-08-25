"""Retrieve passages from campus documents, and hand them over as material.

The lane is thin on purpose. Ranking, chunking and what counts as a match are
the retrieval service's job; what belongs here is the same contract every other
lane keeps — return what was found, return an empty list when nothing was, and
raise when the lookup could not happen at all.

Those three are different answers and the shapes say which:
`{"results": [...]}` found passages, `{"results": []}` searched and there are
none, and a raise means nothing was searched. Only the last one is a failure.

Every passage is scraped text the service itself marks untrusted, and it goes
on to be read by a model. Nothing here parses it, matches on it, or lets it
change what happens next — it is carried through as material and labelled as
material, which is what lets BRAIN #3 be told to treat it as quoted, not as
something addressed to it.
"""

from __future__ import annotations

from rockygpt_brain.brain.execute.schema import DOCUMENTS, Execution
from rockygpt_brain.brain.plan.schema import Plan
from rockygpt_brain.errors import Unavailable
from rockygpt_brain.services.rag.client import RagPort, RagUnavailable

#: Enough passages to answer from, few enough that the answer stays about the
#: question. Beyond a handful, extra chunks are noise the model has to argue
#: itself out of rather than evidence it can use.
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
