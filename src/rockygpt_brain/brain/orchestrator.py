"""The chat pipeline: deterministic safety short-circuit, then a bounded,
single-deadline tool-calling loop that always ends in a schema-valid
`ChatOutcome` — never an unhandled exception, a hang past the UI's
60-second budget, or a partially-fabricated-looking answer.

See DESIGN.md §4-5 for the overall design and THREAT_MODEL.md §3.4/§3.6 for
the guarantees this module is responsible for upholding: a citation the
caller sees always traces to a real tool result from this turn
(brain/grounding.py), and an active emergency or suicidal-intent message
never reaches the model at all (brain/safety.py).

Every model turn is checked, in order, *before* any of its calls are acted
on: (1) tool-call ids must be unique both within the turn and against every
id seen in this whole conversation so far; (2) the turn's call count must
fit the remaining per-conversation tool-call budget (`MAX_TOTAL_TOOL_CALLS`)
— an absolute cap with no exceptions, `submit_answer` included; a final
slot is *reserved* (the model is forced to call `submit_answer` once only
one slot remains, not after the cap is already exhausted) so a solo
forced-submit call still fits the same uniform budget check rather than
needing a special case; (3) `submit_answer` must be the *only* call in its
turn. Any failure of these — or a `citedSourceId` that doesn't resolve, or
a transcript that has grown past its byte cap — falls back to a safe
default answer rather than salvaging a partial or ambiguous result.

Within a single turn, a call repeating an earlier call's exact name and
arguments is answered from that earlier result instead of re-querying the
data service. It still counts against the tool-call budget and still
appears in the log (flagged `cached`); only the redundant network request
is skipped.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from rockygpt_brain.brain.answer import (
    SUBMIT_ANSWER_TOOL_NAME,
    SUBMIT_ANSWER_TOOL_SPEC,
    SubmitAnswerArguments,
    parse_submit_answer,
)
from rockygpt_brain.brain.grounding import ProvenanceRegistry
from rockygpt_brain.brain.model_client import ModelClient
from rockygpt_brain.brain.prompts import build_system_prompt
from rockygpt_brain.brain.safety import SafetyClassification, classify_safety
from rockygpt_brain.brain.time_context import TimeContext, resolve_time_context
from rockygpt_brain.brain.tools import (
    TOOL_HANDLERS,
    declared_argument_keys,
    execute_tool,
    openai_tool_specs,
)
from rockygpt_brain.data_client.client import DataServiceClient
from rockygpt_brain.data_client.errors import DataClientError
from rockygpt_brain.data_client.models import normalize_source
from rockygpt_brain.schemas.chat import ChatRequest
from rockygpt_brain.schemas.common import Citation, UiAction

MAX_TOOL_ITERATIONS = 4
MAX_TOTAL_TOOL_CALLS = 20
MAX_TRANSCRIPT_BYTES = 200_000

# Single outer deadline for the whole grounded-turn path (safety turns are
# already bounded by the data client's own per-call httpx timeout and never
# loop). Left with real margin under the UI's documented 60-second upstream
# timeout for request parsing, persistence, and network overhead — this
# replaces relying on up to MAX_TOOL_ITERATIONS sequential per-call model
# timeouts, which could otherwise sum past 60s on their own. asyncio.timeout
# cancels the in-flight call cleanly, so this is cancellation-safe.
OUTER_DEADLINE_SECONDS = 45.0

FALLBACK_ANSWER = (
    "I'm sorry, I wasn't able to put together a reliable answer to that just now. "
    "Could you try rephrasing your question, or asking again in a moment?"
)

# Every tool name that reaches a log/persistence-facing field is mapped
# through this allowlist first; anything else (a hallucinated or malformed
# model-provided name) becomes the fixed literal "unknown" rather than
# being retained verbatim.
_KNOWN_TOOL_NAMES = frozenset({*TOOL_HANDLERS.keys(), SUBMIT_ANSWER_TOOL_NAME})


def _tool_log_name(name: str) -> str:
    return name if name in _KNOWN_TOOL_NAMES else "unknown"


@dataclass(slots=True)
class ChatOutcome:
    answer: str
    route: str
    citations: list[Citation]
    ui_actions: list[UiAction]
    suggested_questions: list[str]
    tools_invoked: list[str]
    tool_calls_log: list[dict[str, Any]]
    debug_info: dict[str, Any] = field(default_factory=dict)


async def run_chat_turn(
    *,
    request: ChatRequest,
    model_client: ModelClient,
    data_client: DataServiceClient,
) -> ChatOutcome:
    safety = classify_safety(request.message)
    if safety is not None:
        return await _safety_outcome(safety, data_client)

    try:
        async with asyncio.timeout(OUTER_DEADLINE_SECONDS):
            return await _run_grounded_turn(
                request=request, model_client=model_client, data_client=data_client
            )
    except TimeoutError:
        return _fallback_outcome(tools_invoked=[], tool_calls_log=[])


async def _run_grounded_turn(
    *,
    request: ChatRequest,
    model_client: ModelClient,
    data_client: DataServiceClient,
) -> ChatOutcome:
    time_context = resolve_time_context(now=request.now, timezone_name=request.timezone)
    registry = ProvenanceRegistry()
    system_prompt = build_system_prompt(
        time_context=time_context,
        style_mode=request.style_mode,
        response_mode=request.response_mode,
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn in request.history:
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": request.message})

    tools = [*openai_tool_specs(), SUBMIT_ANSWER_TOOL_SPEC]

    tools_invoked: list[str] = []
    tool_calls_log: list[dict[str, Any]] = []
    seen_call_ids: set[str] = set()
    total_tool_calls = 0
    # Results of this turn's data calls, keyed by the exact (name,
    # arguments) pair that produced them. Models repeat a call verbatim
    # fairly often, and every repeat is otherwise a real round trip to
    # rockygpt-data spending latency against OUTER_DEADLINE_SECONDS to
    # learn nothing new. `at` is injected server-side from this turn's
    # fixed TimeContext, so identical arguments really do mean an
    # identical request. Scoped to this turn only -- never across
    # requests, where the underlying campus data may well have changed.
    tool_result_cache: dict[tuple[str, str], dict[str, Any]] = {}

    for iteration in range(MAX_TOOL_ITERATIONS):
        # Reserve the final slot: force submit_answer once only one slot of
        # budget remains, not after the cap is already exhausted, so the
        # forced solo-submit call always fits the same uniform budget check
        # below rather than needing an exception to it.
        force_final = (
            iteration == MAX_TOOL_ITERATIONS - 1
            or total_tool_calls >= MAX_TOTAL_TOOL_CALLS - 1
        )
        force_tool = SUBMIT_ANSWER_TOOL_NAME if force_final else None

        model_turn = await model_client.complete(
            messages=messages, tools=tools, force_tool=force_tool
        )

        if not model_turn.tool_calls:
            # A plain-text turn is the model answering directly instead of
            # calling submit_answer — common once tool results are already
            # in the transcript. That content is a real answer, so
            # discarding it here would fall back with a usable answer in
            # hand. Re-prompt once with submit_answer forced, which routes
            # the same answer back through the validated citation path
            # rather than around it. Only a model that still refuses the
            # tool, or a turn that is already forced, falls back.
            if force_final or not model_turn.content:
                break
            messages.append({"role": "assistant", "content": model_turn.content})
            model_turn = await model_client.complete(
                messages=messages, tools=tools, force_tool=SUBMIT_ANSWER_TOOL_NAME
            )
            if not model_turn.tool_calls:
                break

        call_ids = [call.id for call in model_turn.tool_calls]
        if len(call_ids) != len(set(call_ids)) or seen_call_ids.intersection(call_ids):
            break  # duplicate tool-call id, within this batch or vs. an earlier one

        # Absolute, exception-free budget: every batch, submit_answer
        # included, must fit what's left of MAX_TOTAL_TOOL_CALLS.
        remaining_budget = MAX_TOTAL_TOOL_CALLS - total_tool_calls
        if len(model_turn.tool_calls) > remaining_budget:
            break

        seen_call_ids.update(call_ids)
        total_tool_calls += len(model_turn.tool_calls)

        submit_calls = [
            call for call in model_turn.tool_calls if call.name == SUBMIT_ANSWER_TOOL_NAME
        ]
        if submit_calls:
            # submit_answer must be the *only* call in its turn: multiple
            # submit_answer calls, or submit_answer mixed with data calls,
            # is an ambiguous/conflicting turn, not something to arbitrate.
            if len(submit_calls) > 1 or len(model_turn.tool_calls) > 1:
                break
            outcome = _try_finalize(
                submit_calls[0],
                registry=registry,
                tools_invoked=tools_invoked,
                tool_calls_log=tool_calls_log,
            )
            if outcome is not None:
                return outcome
            break  # malformed arguments or an unresolved citedSourceId

        messages.append(
            {
                "role": "assistant",
                "content": model_turn.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments_json},
                    }
                    for call in model_turn.tool_calls
                ],
            }
        )

        for call in model_turn.tool_calls:
            log_name = _tool_log_name(call.name)
            tools_invoked.append(log_name)
            cache_key = (call.name, call.arguments_json)
            cached_result = tool_result_cache.get(cache_key)
            if cached_result is None:
                result = await _execute_tool_call(
                    call, data_client=data_client, time_context=time_context, registry=registry
                )
                tool_result_cache[cache_key] = result
            else:
                # Provenance is unaffected: the first execution of this
                # exact call already registered its sources into this same
                # turn's registry, so every sourceId the model sees here
                # stays resolvable. Re-running the call would register the
                # identical set again, not a larger one.
                result = cached_result
            # Only a fixed, bounded result category is retained — never the
            # raw argument values. The model can copy arbitrary user text
            # (or an attempted secret) into an argument like `q`; logging
            # that verbatim would defeat the point of redacting stored chat
            # text elsewhere (security/redaction.py, THREAT_MODEL.md §3.3).
            result_category = result.get("error", "ok") if isinstance(result, dict) else "ok"
            log_entry: dict[str, Any] = {"tool": log_name, "result": result_category}
            # Argument *names* only, filtered through the tool's own schema.
            # An optional filter the model did or did not supply decides
            # whether the answer was even reachable, and nothing in the
            # response text records that choice. Values stay excluded, exactly
            # as before.
            argument_keys = declared_argument_keys(
                call.name, _safe_json_loads(call.arguments_json)
            )
            if argument_keys:
                log_entry["argumentKeys"] = argument_keys
            if cached_result is not None:
                # Still one logged call and still one unit of budget spent
                # — the model did ask twice, and hiding that would make the
                # budget accounting unreadable to an operator. The flag
                # records only that no second request left the service.
                log_entry["cached"] = True
            tool_calls_log.append(log_entry)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        if _transcript_size(messages) > MAX_TRANSCRIPT_BYTES:
            break

    return _fallback_outcome(tools_invoked=tools_invoked, tool_calls_log=tool_calls_log)


def _transcript_size(messages: list[dict[str, Any]]) -> int:
    return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))


async def _execute_tool_call(
    call: Any,
    *,
    data_client: DataServiceClient,
    time_context: TimeContext,
    registry: ProvenanceRegistry,
) -> dict[str, Any]:
    arguments = _safe_json_loads(call.arguments_json)
    try:
        return await execute_tool(
            call.name,
            arguments,
            client=data_client,
            time_context=time_context,
            registry=registry,
        )
    except DataClientError:
        return {"error": "data_unavailable"}


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None


def _try_finalize(
    submit_call: Any,
    *,
    registry: ProvenanceRegistry,
    tools_invoked: list[str],
    tool_calls_log: list[dict[str, Any]],
) -> ChatOutcome | None:
    parsed = parse_submit_answer(submit_call.arguments_json)
    if parsed is None:
        return None
    citations = registry.resolve(parsed.cited_source_ids)
    if citations is None:
        # A citedSourceId that this turn's tools never actually produced:
        # fail the whole answer rather than silently dropping it and
        # returning a still-"standard"-routed answer with fewer citations
        # than the model claimed.
        return None
    return _finalize(
        parsed, citations=citations, tools_invoked=tools_invoked, tool_calls_log=tool_calls_log
    )


def _finalize(
    parsed: SubmitAnswerArguments,
    *,
    citations: list[Citation],
    tools_invoked: list[str],
    tool_calls_log: list[dict[str, Any]],
) -> ChatOutcome:
    # "standard" is what the UI presents as a verified campus answer, so it
    # has to be one: an answer with nothing to cite is unverified no matter
    # what the model labelled it, and the honest route for that is
    # "ungrounded". Downgrading is always safe in this direction — the
    # opposite (promoting an uncited answer to "standard") is the one that
    # would overstate what the brain checked. brain/answer.py already
    # rejects the mirror case, "ungrounded" carrying citations.
    route = "ungrounded" if parsed.route == "standard" and not citations else parsed.route
    return ChatOutcome(
        answer=parsed.answer_markdown,
        route=route,
        citations=citations,
        ui_actions=parsed.ui_actions,
        suggested_questions=list(parsed.suggested_questions),
        tools_invoked=tools_invoked,
        tool_calls_log=tool_calls_log,
        debug_info={"tool_call_count": len(tool_calls_log)},
    )


def _fallback_outcome(
    *, tools_invoked: list[str], tool_calls_log: list[dict[str, Any]]
) -> ChatOutcome:
    return ChatOutcome(
        answer=FALLBACK_ANSWER,
        route="ungrounded",
        citations=[],
        ui_actions=[],
        suggested_questions=[],
        tools_invoked=tools_invoked,
        tool_calls_log=tool_calls_log,
        debug_info={"fallback": True, "tool_call_count": len(tool_calls_log)},
    )


async def _safety_outcome(
    classification: SafetyClassification, data_client: DataServiceClient
) -> ChatOutcome:
    try:
        resources = await data_client.safety_resources()
    except DataClientError:
        resources = None

    citations: list[Citation] = []
    if classification.reason == "active_emergency":
        lines = [
            "**If this is an active emergency, call 911 immediately.**",
            "",
            "Get to safety if you can, and stay with emergency services on the line "
            "until help arrives.",
        ]
        source = normalize_source(resources.safety_source) if resources else None
        if source is not None:
            # Fixed prose only — an untrusted data-service Source.title is
            # never interpolated into Markdown answer text (it could itself
            # contain Markdown syntax and render as something other than
            # plain text). The real title/url are carried entirely by the
            # separately-structured Citation the UI renders on its own.
            lines += ["", "See the campus safety resource listed in Sources."]
            citations.append(
                Citation(
                    source_id=source.source_id,
                    title=source.title,
                    url=source.url,
                    collected_at=source.collected_at,
                )
            )
    else:
        lines = [
            "**If you are in crisis, please call or text 988 (Suicide & Crisis Lifeline) "
            "right now.** You deserve support, and help is available 24/7.",
            "",
            "If you are in immediate danger, call 911.",
        ]
        source = normalize_source(resources.counseling_source) if resources else None
        if source is not None:
            lines += ["", "See the campus counseling resource listed in Sources."]
            citations.append(
                Citation(
                    source_id=source.source_id,
                    title=source.title,
                    url=source.url,
                    collected_at=source.collected_at,
                )
            )

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
