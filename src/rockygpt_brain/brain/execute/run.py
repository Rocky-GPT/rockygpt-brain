from __future__ import annotations

from datetime import datetime

from rockygpt_brain.brain.execute.schema import SAFETY, Execution
from rockygpt_brain.brain.plan.schema import Lane, Plan
from rockygpt_brain.lanes import code, general, rag
from rockygpt_brain.safety.enforce import required
from rockygpt_brain.services.data import DataPort
from rockygpt_brain.services.rag.client import RagPort
from rockygpt_brain.services.web import WebPort


async def run(
    checked: Plan, now: datetime, data: DataPort, web: WebPort, documents: RagPort
) -> Execution:
    if checked.safety:
        return Execution(SAFETY, results=required(checked.safety))

    if checked.lane is Lane.GENERAL:
        return await general.run.run(checked, web)
    if checked.lane is Lane.CODE:
        return await code.run.run(checked, now, data)
    return await rag.run.run(checked, documents)
