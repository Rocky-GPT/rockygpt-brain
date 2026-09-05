"""One bounded model/tool loop over one published campus release."""

import json
import re
from datetime import datetime, timedelta
from importlib.resources import files
from time import monotonic
from typing import Any, cast

from openai import OpenAI, Timeout
from openai.types.responses import ResponseInputParam, ToolParam
from pydantic import ValidationError

from rockygpt_brain.contracts import Answer, ChatMessage, EvidenceReview
from rockygpt_brain.data import COLLECTIONS, CampusData, ReadQuery, SearchQuery

INSTRUCTIONS = files("rockygpt_brain").joinpath("prompt.md").read_text(encoding="utf-8")
REVIEW_INSTRUCTIONS = files("rockygpt_brain").joinpath("review.md").read_text(encoding="utf-8")
MAX_DRAFT_CALLS = 6
MAX_MODEL_CALLS = 8
MAX_TOOL_CALLS = 12
TURN_SECONDS = 50.0
REVIEW_RESERVE_SECONDS = 8.0
ANSWER_RESERVE_SECONDS = 20.0


class InvalidAnswer(Exception):
    """The provider did not produce a safe, complete output contract."""

    def __init__(self, message: str, code: str = "invalid_answer") -> None:
        super().__init__(message)
        self.code = code


def function_tool(name: str, description: str, schema: dict[str, Any]) -> ToolParam:
    # Optional arguments are required-but-nullable in strict Responses schemas.
    schema = dict(schema)
    schema["required"] = list(schema["properties"])
    for value in schema["properties"].values():
        value.pop("default", None)
    return cast(
        ToolParam,
        {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": schema,
            "strict": True,
        },
    )


def tool_definitions() -> list[ToolParam]:
    return [
        function_tool(
            "search_campus",
            "Search published official campus evidence. Collections: "
            + ", ".join(COLLECTIONS)
            + ". Use short distinctive terms; an empty query browses a collection. "
            "Dates are campus-local ISO dates. Always supply date_from for menu, "
            "campus_hours, dining_hours, shuttle and events, using the requested date "
            "or the supplied current campus date. Do not put schedule dates only in keywords. "
            "Read returned records for missing details. "
            "Search each requested subject; reformulate if no relevant results. "
            "A no-match result may include discovery_titles from a small published collection. "
            "Choose relevant names by meaning and retrieve their records before citing them.",
            SearchQuery.model_json_schema(),
        ),
        function_tool(
            "read_campus",
            "Read details of evidence ids already returned by search_campus.",
            ReadQuery.model_json_schema(),
        ),
    ]


def render_answer(answer: Answer, evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """References resolve to retrieved sources; links never come from model text."""
    paragraphs: list[str] = []
    citations: dict[str, dict[str, Any]] = {}
    for part in answer.parts:
        if not part.text.strip() or re.search(
            r"https?://|www\.|\]\(|\]\s*\[|\]:\s*\S|<(?:[a-z][a-z0-9+.-]*:|//)",
            part.text,
            re.IGNORECASE,
        ):
            raise InvalidAnswer(
                "Model text contains an unvalidated link or is blank", "answer_text"
            )
        if part.kind == "campus_fact" and not part.evidence_ids:
            raise InvalidAnswer("Campus assertion without evidence", "missing_citation")
        links: dict[str, str] = {}
        for evidence_id in dict.fromkeys(part.evidence_ids):
            record = evidence.get(evidence_id)
            if record is None:
                raise InvalidAnswer("Unknown citation", "unknown_citation")
            if part.kind == "campus_fact" and record["freshness"] not in {"fresh", "static"}:
                raise InvalidAnswer("Campus assertion uses stale or unknown evidence", "stale_fact")
            url = record["url"]
            if not isinstance(url, str) or not url.startswith("https://"):
                raise InvalidAnswer("Unsafe source URL", "source_url")
            source_title = str(record.get("source_title") or record["title"])
            title = source_title.replace("[", "").replace("]", "")
            safe_url = url.replace("(", "%28").replace(")", "%29").replace(" ", "%20")
            links[url] = f"[{title}]({safe_url})"
            citations[evidence_id] = {
                key: record.get(key)
                for key in (
                    "id",
                    "title",
                    "url",
                    "collection",
                    "collected_at",
                    "freshness",
                    "valid_from",
                    "valid_until",
                    "trust_tier",
                    "limitations",
                )
            }
            citations[evidence_id].update(title=source_title, record_title=record["title"])
        text = part.text.strip()
        if links:
            text += " " + " ".join(links.values())
        paragraphs.append(text)
    rendered = "\n\n".join(paragraphs)
    if len(rendered) > 12000:
        raise InvalidAnswer(
            "Answer is too long for conversation history; shorten it", "answer_length"
        )
    return {
        "answer": rendered,
        "status": answer.status,
        "citations": list(citations.values()),
    }


def review_answer(
    answer: Answer,
    *,
    messages: list[ChatMessage],
    evidence: dict[str, dict[str, Any]],
    client: OpenAI,
    model: str,
    now: datetime,
    timeout: float,
) -> EvidenceReview:
    """A separate context checks every part; draft/tool history cannot approve itself."""
    subjects = {
        record_id: {
            "name": record["title"],
            "kind": (
                "event"
                if record["collection"] == "events"
                or record.get("source_key") == "archway-events"
                # Captured records from the first checkpoint used source titles.
                or record.get("source_title") == "Archway Events"
                else record["collection"]
            ),
        }
        for record_id, record in evidence.items()
    }
    # A summary can reuse citations already visible in this answer. Explicit
    # citations keep their own scope; unrelated records cannot replace them.
    citation_scope: dict[int, list[str]] = {}
    preceding_citations: dict[str, None] = {}
    for index, answer_part in enumerate(answer.parts):
        citation_scope[index] = list(dict.fromkeys(answer_part.evidence_ids))
        if not citation_scope[index] and answer_part.kind != "campus_fact":
            citation_scope[index] = list(preceding_citations)
        preceding_citations.update(dict.fromkeys(answer_part.evidence_ids))
    event_citations = {
        index: [
            record_id
            for record_id in record_ids
            if subjects.get(record_id, {}).get("kind") == "event"
        ]
        for index, record_ids in citation_scope.items()
    }
    response = client.responses.create(
        model=model,
        instructions=REVIEW_INSTRUCTIONS,
        input=json.dumps(
            {
                "conversation": [message.model_dump() for message in messages],
                "campus_time": now.isoformat(),
                "campus_weekday": now.strftime("%A"),
                "campus_calendar_week": [
                    str(now.date() - timedelta(days=now.weekday())),
                    str(now.date() + timedelta(days=6 - now.weekday())),
                ],
                "candidate": answer.model_dump(),
                "evidence": list(evidence.values()),
                "evidence_subjects": subjects,
                "citation_scope": citation_scope,
                "event_citations": event_citations,
            },
            default=str,
            ensure_ascii=False,
        ),
        tools=[],
        tool_choice="none",
        text={
            "format": {
                "type": "json_schema",
                "name": "evidence_review",
                "schema": EvidenceReview.model_json_schema(),
                "strict": True,
            }
        },
        reasoning={"effort": "low"},
        max_output_tokens=4096,
        store=False,
        timeout=Timeout(timeout, connect=min(2.0, timeout)),
    )
    if response.status != "completed":
        raise InvalidAnswer("Incomplete evidence review", "incomplete_review")
    try:
        review = EvidenceReview.model_validate_json(response.output_text)
    except ValidationError as error:
        raise InvalidAnswer("Invalid evidence review", "invalid_review") from error
    if sorted(part.part_index for part in review.parts) != list(range(len(answer.parts))):
        raise InvalidAnswer(
            "Review did not cover every answer part exactly once", "review_coverage"
        )
    for part in review.parts:
        if part.unverified_premises:
            part.verdict = "unsupported_claim"
            part.reason = ("Missing factual support: " + "; ".join(part.unverified_premises))[:400]
        # Citation membership and source kind come from code, not an ID list
        # echoed by the reviewer. Event facts are about that activity only.
        if event_citations[part.part_index] and part.uses_event_for_entity:
            part.verdict = "wrong_scope"
            part.reason = (
                "Event evidence cannot establish general attributes of a referenced "
                "facility or organization. Use direct evidence for that entity, or "
                "state that the requested attribute could not be verified."
            )
        if part.infers_food_safety:
            part.verdict = "unsupported_claim"
            part.reason = (
                "Published menu and allergen labels do not establish allergy safety or "
                "relative risk, including when a label is blank. Report the labels and "
                "ask dining staff about ingredients and cross-contact without ranking safety."
            )
        for constraint in part.plan_deadlines:
            if constraint.basis == "standing_service_rule":
                continue
            deadline = constraint.latest_usable_at
            if deadline is None or deadline.tzinfo is None:
                raise InvalidAnswer("Dated plan lacks a timezone-aware deadline", "invalid_review")
            if deadline <= now:
                part.verdict = "wrong_context"
                part.reason = (
                    f"This proposed action's latest usable time is {deadline.isoformat()}, "
                    f"which has passed at campus time {now.isoformat()}. Describe it as past "
                    "or offer an option that can still be followed, using published evidence."
                )
    return review


def run_turn(
    messages: list[ChatMessage],
    *,
    client: OpenAI,
    data: CampusData,
    model: str,
    now: datetime,
) -> dict[str, Any]:
    started = monotonic()
    data.deadline = started + TURN_SECONDS - ANSWER_RESERVE_SECONDS
    history: list[Any] = [message.model_dump() for message in messages]
    evidence: dict[str, dict[str, Any]] = {}
    trace: list[dict[str, Any]] = []
    # A tool limit closes retrieval, not answer recovery. Review capacity is
    # separate and every call still shares the same wall-clock deadline.
    tool_slots_used = 0
    tool_executions = 0
    draft_calls = 0
    review_calls = 0
    validation_failures: list[str] = []
    answer_only = False
    dataset_version: str | None = None
    week_start = now.date() - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=6)
    instructions = INSTRUCTIONS + (
        f"\nCurrent campus time: {now.isoformat()} (America/New_York).\n"
        f"Today is {now.strftime('%A, %B %d, %Y')}. "
        f"The current campus calendar week is {week_start} through {week_end}.\n"
    )
    tools = tool_definitions()
    for round_index in range(MAX_DRAFT_CALLS):
        if draft_calls + review_calls > MAX_MODEL_CALLS - 2:
            raise InvalidAnswer("No capacity for a draft and its review", "answer_budget")
        remaining = TURN_SECONDS - (monotonic() - started)
        if remaining <= REVIEW_RESERVE_SECONDS:
            raise TimeoutError("Insufficient time for an answer and evidence review")
        # Reserve the last two draft calls for synthesis and contract repair.
        # Semantic repair also needs a fresh review; it never reopens retrieval.
        answer_only = (
            answer_only
            or round_index >= MAX_DRAFT_CALLS - 2
            or draft_calls + review_calls >= MAX_MODEL_CALLS - 2
            or tool_slots_used >= MAX_TOOL_CALLS
            or remaining <= ANSWER_RESERVE_SECONDS
        )
        draft_calls += 1
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=cast(ResponseInputParam, list(history)),
            tools=tools,
            tool_choice="none" if answer_only else "auto",
            parallel_tool_calls=True,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "student_answer",
                    "schema": Answer.model_json_schema(),
                    "strict": True,
                }
            },
            max_output_tokens=2400,
            store=False,
            timeout=Timeout(
                min(30.0, remaining - REVIEW_RESERVE_SECONDS),
                connect=min(2.0, remaining - REVIEW_RESERVE_SECONDS),
            ),
        )
        if response.status != "completed":
            raise InvalidAnswer("Incomplete model response", "incomplete_draft")
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            try:
                candidate = Answer.model_validate_json(response.output_text)
                result = render_answer(candidate, evidence)
            except (ValidationError, InvalidAnswer) as error:
                code = "answer_schema" if isinstance(error, ValidationError) else error.code
                validation_failures.append(code)
                if round_index == MAX_DRAFT_CALLS - 1:
                    raise InvalidAnswer("Invalid final answer", code) from error
                detail = (
                    json.dumps(
                        [
                            {"field": item["loc"], "error": item["type"]}
                            for item in error.errors(include_input=False, include_context=False)[:3]
                        ]
                    )
                    if isinstance(error, ValidationError)
                    else str(error)
                )
                history.extend(response.output)
                history.append(
                    {
                        "role": "developer",
                        "content": "Your answer failed server validation: "
                        + detail[:400]
                        + ". Return a corrected answer. Use only retrieved evidence, separate "
                        "limitations, and omit URLs. If evidence is missing, say so.",
                    }
                )
                continue
            for review_attempt in range(2):
                remaining = TURN_SECONDS - (monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("Turn deadline exceeded before evidence review")
                if draft_calls + review_calls >= MAX_MODEL_CALLS:
                    raise InvalidAnswer("No capacity for evidence review", "answer_budget")
                review_calls += 1
                try:
                    review = review_answer(
                        candidate,
                        messages=messages,
                        evidence=evidence,
                        client=client,
                        model=model,
                        now=now,
                        timeout=min(20.0, remaining),
                    )
                except InvalidAnswer as error:
                    if error.code not in {"invalid_review", "incomplete_review", "review_coverage"}:
                        raise
                    validation_failures.append(error.code)
                    if review_attempt == 1:
                        raise
                else:
                    break
            rejected = [part for part in review.parts if part.verdict != "supported"]
            if rejected:
                validation_failures.extend(part.verdict for part in rejected)
                if (
                    draft_calls + review_calls > MAX_MODEL_CALLS - 2
                    or round_index == MAX_DRAFT_CALLS - 1
                ):
                    raise InvalidAnswer("Answer failed evidence review", "unsupported_answer")
                answer_only = True
                history.extend(response.output)
                history.append(
                    {
                        "role": "developer",
                        "content": "The evidence review rejected this draft. Correct the "
                        "identified claims using the retrieved evidence, preserve supported "
                        "parts, and explicitly state any missing information. The revision "
                        "must pass a new review. Review findings: " + review.model_dump_json(),
                    }
                )
                continue
            if monotonic() - started >= TURN_SECONDS:
                raise TimeoutError("Turn deadline exceeded during evidence review")
            return {
                **result,
                "model": response.model,
                "datasetVersion": dataset_version,
                "trace": trace,
                "metrics": {
                    "modelCalls": draft_calls + review_calls,
                    "draftCalls": draft_calls,
                    "reviewCalls": review_calls,
                    "toolRequests": len(trace),
                    "toolExecutions": tool_executions,
                    "validationFailures": validation_failures,
                },
                "elapsedMs": round((monotonic() - started) * 1000),
            }
        history.extend(response.output)
        for call in calls:
            tool_started = monotonic()
            arguments: dict[str, Any] = {}
            if (
                answer_only
                or tool_slots_used >= MAX_TOOL_CALLS
                or monotonic() - started >= TURN_SECONDS - ANSWER_RESERVE_SECONDS
            ):
                output: dict[str, Any] = {"status": "unavailable", "reason": "tool_budget"}
            else:
                tool_slots_used += 1
                try:
                    if call.name == "search_campus":
                        query = SearchQuery.model_validate_json(call.arguments)
                        arguments = query.model_dump(mode="json")
                        tool_executions += 1
                        output = data.search(query)
                    elif call.name == "read_campus":
                        read = ReadQuery.model_validate_json(call.arguments)
                        arguments = read.model_dump(mode="json")
                        tool_executions += 1
                        output = data.read(read)
                    else:
                        output = {"status": "invalid_request", "reason": "unknown_tool"}
                except ValidationError as error:
                    output = {
                        "status": "invalid_request",
                        "reason": "invalid_arguments",
                        "details": [
                            {"field": ".".join(map(str, item["loc"])), "message": item["msg"]}
                            for item in error.errors(
                                include_input=False, include_context=False, include_url=False
                            )[:3]
                        ],
                    }
                except Exception:
                    # Do not expose connection strings, SQL, or provider errors.
                    output = {"status": "unavailable", "reason": "campus_data_unavailable"}
            dataset_version = output.get("dataset_version", dataset_version)
            for record in output.get("records", []):
                previous = evidence.get(record["id"])
                # Same release/record, different excerpt bounds: an overlapping
                # search must not erase details already delivered by read_campus.
                if previous is None or len(json.dumps(record, default=str)) >= len(
                    json.dumps(previous, default=str)
                ):
                    evidence[record["id"]] = record
            trace.append(
                {
                    "tool": call.name,
                    "arguments": arguments,
                    "status": output["status"],
                    "result_count": len(output.get("records", [])),
                    "total_matches": output.get("total_matches"),
                    "truncated": output.get("truncated"),
                    "reason": output.get("reason"),
                    "elapsed_ms": round((monotonic() - tool_started) * 1000),
                }
            )
            history.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(output, default=str, ensure_ascii=False),
                }
            )
    raise InvalidAnswer("Model did not finish within the answer budget", "answer_budget")
