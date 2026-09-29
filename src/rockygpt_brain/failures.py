"""Every way a turn can fail, what the student is told, and the help that always comes with it.

Written by code with no model call, so a failure still helps when models, money or
data are out. Wording and statuses match the old Brain (c00eb91 api/app.py).
"""

from typing import NamedTuple

from rockygpt_brain.contract import (
    EMERGENCY_TEXT,
    RETRYABLE,
    EmergencyHelp,
    ErrorBody,
    ErrorCode,
    FailureReply,
    Source,
)

TRY_AGAIN = "Rocky couldn't produce a reliable answer just now. Please try again."


class Failure(NamedTuple):
    status: int
    message: str


FAILURES: dict[ErrorCode, Failure] = {
    "not_ready": Failure(503, "RockyGPT's new Brain can't answer questions yet."),
    "busy": Failure(429, TRY_AGAIN),
    "rate_limited": Failure(429, TRY_AGAIN),
    "budget_exhausted": Failure(
        429, "RockyGPT's monthly AI allowance is exhausted. Use the official campus resources."),
    "model_quota_exhausted": Failure(
        429, "RockyGPT is currently unavailable. Please use Ramapo's official resources for "
        "campus information."),
    "model_timeout": Failure(504, TRY_AGAIN),
    "model_unreachable": Failure(503, TRY_AGAIN),
    "model_provider_error": Failure(502, TRY_AGAIN),
    "model_not_configured": Failure(
        503, "RockyGPT is unavailable until its service configuration is restored."),
    "invalid_model_output": Failure(502, TRY_AGAIN),
    "context_limit": Failure(
        422, "This conversation exceeds the supported context limit. Start a shorter chat."),
    "retrieval_context_limit": Failure(
        422, "The information needed for this answer exceeds RockyGPT's processing limit. "
        "Try narrowing the request to one topic, place, or date."),
    "turn_cost_limit": Failure(
        422, "This request exceeds RockyGPT's per-answer processing allowance. Please ask a "
        "more focused question."),
    "model_call_limit": Failure(
        422, "This request exceeds RockyGPT's per-answer processing allowance. Please ask a "
        "more focused question."),
    "request_cancelled": Failure(499, "The request was cancelled."),
    "accounting_unavailable": Failure(
        503, "RockyGPT is unavailable until its service configuration is restored."),
    "accounting_paused": Failure(
        503, "RockyGPT is unavailable until its service configuration is restored."),
    "internal_error": Failure(500, TRY_AGAIN),
}


def failure(
    code: ErrorCode,
    request_id: str,
    *,
    reset_at: str | None = None,
    resources: list[Source] | None = None,
) -> tuple[int, FailureReply]:
    """The HTTP status and body. Nobody waits on a cancelled request, so it alone
    goes without the emergency help."""
    status, message = FAILURES[code]
    return status, FailureReply(
        error=ErrorBody(
            code=code,
            message=message,
            retryable=code in RETRYABLE,
            resetAt=reset_at,
            resources=resources or None,
            emergency=None if code == "request_cancelled" else EmergencyHelp(text=EMERGENCY_TEXT),
        ),
        reason=code,
        requestId=request_id,
    )
