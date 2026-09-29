"""One turn, step by step. Each milestone adds its step here, in the plan's order."""

from typing import NamedTuple

from rockygpt_brain.context import Context
from rockygpt_brain.contract import ChatReply, FailureReply, SafetyBlock
from rockygpt_brain.failures import failure
from rockygpt_brain.safety import safety_block, said_danger

NOT_YET = "RockyGPT's new Brain can't answer the rest of your question yet."


class TurnResult(NamedTuple):
    status: int
    body: ChatReply | FailureReply
    # Sent to a streaming app the moment danger is read, before anything else.
    safety: SafetyBlock | None = None


def answer_turn(context: Context, request_id: str) -> TurnResult:
    danger = said_danger(context.question)
    if danger is None:
        return TurnResult(*failure("not_ready", request_id))
    block = safety_block(danger)
    return TurnResult(200, ChatReply(
        answer=f"{block.answer}\n\n{NOT_YET}",
        status="partial",
        citations=block.citations,
        requestId=request_id,
    ), block)
