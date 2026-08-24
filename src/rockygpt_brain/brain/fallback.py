"""Why a turn ended in the canned apology, and the outcome that carries it.

A fixed vocabulary — these strings are literals in this module, never model-
or user-supplied text — so they can be persisted and read back through the
operator channel without widening what is retained about a conversation.
"""

from __future__ import annotations

from typing import Any

from rockygpt_brain.brain.outcome import ChatOutcome

FALLBACK_ANSWER = (
    "I'm sorry, I wasn't able to put together a reliable answer to that just now. "
    "Could you try rephrasing your question, or asking again in a moment?"
)

FallbackReason = str

NO_TOOL_CALLS = "no_tool_calls"
FORCED_SUBMIT_REFUSED = "forced_submit_refused"
DUPLICATE_CALL_ID = "duplicate_call_id"
BUDGET_EXCEEDED = "budget_exceeded"
AMBIGUOUS_SUBMIT_BATCH = "ambiguous_submit_batch"
# Two very different failures used to share one label. A submission whose
# arguments do not validate is a formatting problem; a submission citing a
# sourceId this turn never produced is a grounding problem with its own
# deliberate, security-relevant handling (THREAT_MODEL 3.4). Telling them apart
# is the whole point of the trace.
SUBMIT_MALFORMED = "submit_malformed"
SUBMIT_CITATION_UNRESOLVED = "submit_citation_unresolved"
TRANSCRIPT_TOO_LARGE = "transcript_too_large"
ITERATIONS_EXHAUSTED = "iterations_exhausted"
DEADLINE = "deadline"


def fallback_outcome(
    *,
    tools_invoked: list[str],
    tool_calls_log: list[dict[str, Any]],
    reason: FallbackReason = DEADLINE,
    discourse_turns: int = 0,
) -> ChatOutcome:
    return ChatOutcome(
        answer=FALLBACK_ANSWER,
        route="ungrounded",
        citations=[],
        ui_actions=[],
        suggested_questions=[],
        tools_invoked=tools_invoked,
        tool_calls_log=tool_calls_log,
        debug_info={
            "fallback": True,
            "tool_call_count": len(tool_calls_log),
            # Which branch ended the turn, from the fixed vocabulary defined
            # in this module. Enough to tell a refused submission from an
            # exhausted budget without retaining anything about the message.
            "fallback_reason": reason,
            "discourse_turns": discourse_turns,
        },
    )
