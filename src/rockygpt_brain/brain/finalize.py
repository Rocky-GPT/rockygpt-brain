"""Turning an accepted `submit_answer` call into a `ChatOutcome`.

This is the only path by which a model submission becomes an answer the
caller sees, so it is also where the two ways a submission can fail are told
apart: malformed arguments, and a citation the turn never actually produced.
"""

from __future__ import annotations

from typing import Any

from rockygpt_brain.brain.answer import SubmitAnswerArguments, parse_submit_answer_diagnosed
from rockygpt_brain.brain.fallback import (
    SUBMIT_CITATION_UNRESOLVED,
    SUBMIT_MALFORMED,
    FallbackReason,
)
from rockygpt_brain.brain.grounding import ProvenanceRegistry
from rockygpt_brain.brain.model_client import ToolCall
from rockygpt_brain.brain.outcome import ChatOutcome
from rockygpt_brain.schemas.common import Citation


def try_finalize(
    submit_call: ToolCall,
    *,
    registry: ProvenanceRegistry,
    tools_invoked: list[str],
    tool_calls_log: list[dict[str, Any]],
) -> tuple[ChatOutcome | None, FallbackReason | None]:
    parsed, defect = parse_submit_answer_diagnosed(submit_call.arguments_json)
    if parsed is None:
        return None, f"{SUBMIT_MALFORMED}:{defect}"
    citations = registry.resolve(parsed.cited_source_ids)
    if citations is None:
        # A citedSourceId that this turn's tools never actually produced:
        # fail the whole answer rather than silently dropping it and returning
        # a still-"standard"-routed answer with fewer citations than the model
        # claimed. Deliberate, and unchanged (THREAT_MODEL 3.4).
        return None, SUBMIT_CITATION_UNRESOLVED
    return (
        finalize(
            parsed,
            citations=citations,
            tools_invoked=tools_invoked,
            tool_calls_log=tool_calls_log,
        ),
        None,
    )


def finalize(
    parsed: SubmitAnswerArguments,
    *,
    citations: list[Citation],
    tools_invoked: list[str],
    tool_calls_log: list[dict[str, Any]],
) -> ChatOutcome:
    # Both directions of a route/citation mismatch are normalised here rather
    # than rejected, because rejecting costs the whole turn (brain/answer.py).
    #
    # "standard" is what the UI presents as a verified campus answer, so an
    # answer with nothing to cite is downgraded — the honest route for that is
    # "ungrounded". "conversation" is never downgraded: it is *expected* to
    # carry no citations, because the record of what was said is not a campus
    # source, and downgrading it would relabel a verified recollection as
    # something that could not be verified.
    route = "ungrounded" if parsed.route == "standard" and not citations else parsed.route
    # The mirror case: a route meaning "no campus source" arrived carrying
    # citations. Drop them and keep the answer. Never resolved the other way —
    # promoting the route to "standard" would present an answer the model
    # itself called unverified as though it were sourced.
    if route in ("ungrounded", "conversation"):
        citations = []
    return ChatOutcome(
        answer=parsed.answer_markdown,
        route=route,
        citations=citations,
        ui_actions=parsed.ui_actions,
        suggested_questions=list(parsed.suggested_questions),
        tools_invoked=tools_invoked,
        tool_calls_log=tool_calls_log,
        debug_info={"tool_call_count": len(tool_calls_log), "route_submitted": parsed.route},
    )
