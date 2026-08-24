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
from typing import Any

from rockygpt_brain.brain.answer import SUBMIT_ANSWER_TOOL_NAME
from rockygpt_brain.brain.answer_spec import SUBMIT_ANSWER_TOOL_SPEC
from rockygpt_brain.brain.conversation_state import record_for
from rockygpt_brain.brain.discourse import DiscourseRecord, render_discourse
from rockygpt_brain.brain.fallback import (
    AMBIGUOUS_SUBMIT_BATCH,
    BUDGET_EXCEEDED,
    DUPLICATE_CALL_ID,
    FORCED_SUBMIT_REFUSED,
    ITERATIONS_EXHAUSTED,
    NO_TOOL_CALLS,
    SUBMIT_CITATION_UNRESOLVED,
    SUBMIT_MALFORMED,
    TRANSCRIPT_TOO_LARGE,
    FallbackReason,
    fallback_outcome,
)
from rockygpt_brain.brain.finalize import try_finalize
from rockygpt_brain.brain.grounding import ProvenanceRegistry
from rockygpt_brain.brain.model_client import ModelClient
from rockygpt_brain.brain.outcome import ChatOutcome
from rockygpt_brain.brain.prompts import build_system_prompt
from rockygpt_brain.brain.safety import classify_safety
from rockygpt_brain.brain.safety_outcome import safety_outcome
from rockygpt_brain.brain.time_context import TimeContext, resolve_time_context
from rockygpt_brain.brain.tools import (
    TOOL_HANDLERS,
    declared_argument_keys,
    execute_tool,
    openai_tool_specs,
)
from rockygpt_brain.data_client.client import DataServiceClient
from rockygpt_brain.data_client.errors import DataClientError
from rockygpt_brain.schemas.chat import ChatRequest

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


# Every tool name that reaches a log/persistence-facing field is mapped
# through this allowlist first; anything else (a hallucinated or malformed
# model-provided name) becomes the fixed literal "unknown" rather than
# being retained verbatim.
_KNOWN_TOOL_NAMES = frozenset({*TOOL_HANDLERS.keys(), SUBMIT_ANSWER_TOOL_NAME})


def _tool_log_name(name: str) -> str:
    return name if name in _KNOWN_TOOL_NAMES else "unknown"


async def run_chat_turn(
    *,
    request: ChatRequest,
    model_client: ModelClient,
    data_client: DataServiceClient,
) -> ChatOutcome:
    safety = classify_safety(request.message)
    if safety is not None:
        return await safety_outcome(safety, data_client)

    try:
        async with asyncio.timeout(OUTER_DEADLINE_SECONDS):
            return await _run_grounded_turn(
                request=request, model_client=model_client, data_client=data_client
            )
    except TimeoutError:
        return fallback_outcome(tools_invoked=[], tool_calls_log=[])


async def _run_grounded_turn(
    *,
    request: ChatRequest,
    model_client: ModelClient,
    data_client: DataServiceClient,
) -> ChatOutcome:
    time_context = resolve_time_context(now=request.now, timezone_name=request.timezone)
    registry = ProvenanceRegistry()
    # The record outlives the client's ten-entry history window, so a fact from
    # six exchanges ago is still answerable even though the raw turn that
    # carried it is long gone from the request (brain/conversation_state.py).
    discourse = record_for(request.visitor_id, request.conversation_id)
    system_prompt = build_system_prompt(
        time_context=time_context,
        style_mode=request.style_mode,
        response_mode=request.response_mode,
        discourse=render_discourse(discourse) if discourse is not None else None,
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
    fallback_reason: FallbackReason = ITERATIONS_EXHAUSTED
    citation_retry_used = False

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
                fallback_reason = NO_TOOL_CALLS
                break
            messages.append({"role": "assistant", "content": model_turn.content})
            model_turn = await model_client.complete(
                messages=messages, tools=tools, force_tool=SUBMIT_ANSWER_TOOL_NAME
            )
            if not model_turn.tool_calls:
                fallback_reason = FORCED_SUBMIT_REFUSED
                break

        call_ids = [call.id for call in model_turn.tool_calls]
        if len(call_ids) != len(set(call_ids)) or seen_call_ids.intersection(call_ids):
            # duplicate tool-call id, within this batch or vs. an earlier one
            fallback_reason = DUPLICATE_CALL_ID
            break

        # Absolute, exception-free budget: every batch, submit_answer
        # included, must fit what's left of MAX_TOTAL_TOOL_CALLS.
        remaining_budget = MAX_TOTAL_TOOL_CALLS - total_tool_calls
        if len(model_turn.tool_calls) > remaining_budget:
            fallback_reason = BUDGET_EXCEEDED
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
                fallback_reason = AMBIGUOUS_SUBMIT_BATCH
                break
            outcome, reason = try_finalize(
                submit_calls[0],
                registry=registry,
                tools_invoked=tools_invoked,
                tool_calls_log=tool_calls_log,
            )
            if outcome is not None:
                _remember(discourse, request.message, outcome)
                return outcome
            fallback_reason = reason or SUBMIT_MALFORMED

            # An unresolved citation gets one correction before the turn is
            # given up on. The guarantee is untouched — an id this turn never
            # produced is still never accepted — but a single mislabelled id
            # used to cost the student the whole answer, and in traces this was
            # the most common way a good turn was lost. The retry names the ids
            # that do resolve, so the model is correcting against the actual
            # set rather than guessing again. Only once, and only for this
            # cause: a malformed submission is a formatting failure that a
            # re-ask does not inform.
            if reason == SUBMIT_CITATION_UNRESOLVED and not citation_retry_used:
                citation_retry_used = True
                known = registry.known_source_ids()
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": submit_calls[0].id,
                        "content": json.dumps(
                            {
                                "error": "unknown_source_id",
                                "validSourceIds": known,
                                "hint": (
                                    "citedSourceIds must be ids from this turn's "
                                    "tool results. Resubmit citing only ids from "
                                    "validSourceIds, or cite nothing and use route "
                                    "'ungrounded'."
                                ),
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
                messages.insert(
                    len(messages) - 1,
                    {
                        "role": "assistant",
                        "content": model_turn.content,
                        "tool_calls": [
                            {
                                "id": submit_calls[0].id,
                                "type": "function",
                                "function": {
                                    "name": submit_calls[0].name,
                                    "arguments": submit_calls[0].arguments_json,
                                },
                            }
                        ],
                    },
                )
                continue
            break

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
            fallback_reason = TRANSCRIPT_TOO_LARGE
            break

    fallback = fallback_outcome(
        tools_invoked=tools_invoked,
        tool_calls_log=tool_calls_log,
        reason=fallback_reason,
        discourse_turns=len(discourse.spoken) if discourse is not None else 0,
    )
    _remember(discourse, request.message, fallback)
    return fallback


def _remember(
    discourse: DiscourseRecord | None, question: str, outcome: ChatOutcome
) -> None:
    """Note what the student was told, once the turn is settled.

    A fallback is recorded as *withheld* rather than as an answer, so a later
    recap cannot promote "I could not put that together" into a fact Rocky
    never stated. Only the answer text reaches the record — never a tool
    result, and never a citation — so no campus claim can enter the discourse
    path and be mistaken for something that was said.
    """
    if discourse is None:
        return
    withheld = bool(outcome.debug_info.get("fallback"))
    discourse.record(
        question=question,
        answer="" if withheld else outcome.answer,
        withheld=withheld,
        entities=tuple(dict.fromkeys(outcome.tools_invoked)),
    )


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


