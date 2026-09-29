"""One turn, step by step. Each milestone adds its step here, in the plan's order."""

from rockygpt_brain.context import Context
from rockygpt_brain.contract import (
    EMERGENCY_TEXT,
    RETRYABLE,
    ChatReply,
    EmergencyHelp,
    ErrorBody,
    ErrorCode,
    FailureReply,
)

FAILURE_MESSAGES: dict[str, str] = {
    "not_ready": "RockyGPT's new Brain can't answer questions yet.",
}


def answer_turn(context: Context, request_id: str) -> tuple[int, ChatReply | FailureReply]:
    """The HTTP status and body for this turn. No step answers yet."""
    return 503, failure("not_ready", request_id)


def failure(code: ErrorCode, request_id: str) -> FailureReply:
    return FailureReply(
        error=ErrorBody(
            code=code,
            message=FAILURE_MESSAGES[code],
            retryable=code in RETRYABLE,
            emergency=None if code == "request_cancelled" else EmergencyHelp(text=EMERGENCY_TEXT),
        ),
        reason=code,
        requestId=request_id,
    )
