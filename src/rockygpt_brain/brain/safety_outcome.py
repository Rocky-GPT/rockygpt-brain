"""The response to a message that never reaches the model at all.

`brain/safety.py` decides *whether* a message is an active emergency or an
expression of suicidal intent; this builds what the caller gets back when it
is. Answer text here is fixed prose written into this module — an untrusted
data-service `Source.title` is never interpolated into it, since a title can
itself contain Markdown and render as something other than plain text. The
real title and URL travel only in the separately-structured `Citation` the
UI renders on its own (THREAT_MODEL.md §3.6).
"""

from __future__ import annotations

from rockygpt_brain.brain.outcome import ChatOutcome
from rockygpt_brain.brain.safety import SafetyClassification
from rockygpt_brain.data_client.client import DataServiceClient
from rockygpt_brain.data_client.errors import DataClientError
from rockygpt_brain.data_client.models import Source, normalize_source
from rockygpt_brain.schemas.common import Citation

_EMERGENCY_LINES = [
    "**If this is an active emergency, call 911 immediately.**",
    "",
    "Get to safety if you can, and stay with emergency services on the line "
    "until help arrives.",
]

_CRISIS_LINES = [
    "**If you are in crisis, please call or text 988 (Suicide & Crisis Lifeline) "
    "right now.** You deserve support, and help is available 24/7.",
    "",
    "If you are in immediate danger, call 911.",
]

_EMERGENCY_POINTER = "See the campus safety resource listed in Sources."
_COUNSELING_POINTER = "See the campus counseling resource listed in Sources."


def _citation_for(source: Source | None) -> Citation | None:
    if source is None:
        return None
    return Citation(
        source_id=source.source_id,
        title=source.title,
        url=source.url,
        collected_at=source.collected_at,
    )


async def safety_outcome(
    classification: SafetyClassification, data_client: DataServiceClient
) -> ChatOutcome:
    try:
        resources = await data_client.safety_resources()
    except DataClientError:
        resources = None

    if classification.reason == "active_emergency":
        lines = list(_EMERGENCY_LINES)
        pointer = _EMERGENCY_POINTER
        raw_source = resources.safety_source if resources else None
    else:
        lines = list(_CRISIS_LINES)
        pointer = _COUNSELING_POINTER
        raw_source = resources.counseling_source if resources else None

    citations: list[Citation] = []
    citation = _citation_for(normalize_source(raw_source) if raw_source else None)
    if citation is not None:
        lines += ["", pointer]
        citations.append(citation)

    return ChatOutcome(
        answer="\n".join(lines),
        route="safety",
        citations=citations,
        ui_actions=[],
        suggested_questions=[],
        tools_invoked=["get_safety_resources"] if resources is not None else [],
        tool_calls_log=[],
        debug_info={"safety_reason": classification.reason},
    )
