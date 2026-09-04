"""One bounded model/tool loop over one published campus release."""

import json
import re
from datetime import datetime, timedelta
from importlib.resources import files
from time import monotonic
from typing import Any, cast

from openai import OpenAI
from openai.types.responses import ResponseInputParam, ToolParam
from pydantic import ValidationError

from rockygpt_brain.contracts import Answer, ChatMessage
from rockygpt_brain.data import COLLECTIONS, CampusData, ReadQuery, SearchQuery

INSTRUCTIONS = files("rockygpt_brain").joinpath("prompt.md").read_text(encoding="utf-8")
MAX_MODEL_CALLS = 6
MAX_TOOL_CALLS = 12
TURN_SECONDS = 50.0


class InvalidAnswer(Exception):
    """The provider did not produce a safe, complete output contract."""


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
            "Search each requested subject; reformulate if no relevant results.",
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
            raise InvalidAnswer("Model text contains an unvalidated link or is blank")
        if part.kind == "campus_fact" and not part.evidence_ids:
            raise InvalidAnswer("Campus assertion without evidence")
        links: dict[str, str] = {}
        for evidence_id in dict.fromkeys(part.evidence_ids):
            record = evidence.get(evidence_id)
            if record is None:
                raise InvalidAnswer("Unknown citation")
            if part.kind == "campus_fact" and record["freshness"] not in {"fresh", "static"}:
                raise InvalidAnswer("Campus assertion uses stale or unknown evidence")
            url = record["url"]
            if not isinstance(url, str) or not url.startswith("https://"):
                raise InvalidAnswer("Unsafe source URL")
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
        raise InvalidAnswer("Answer is too long for conversation history; shorten it")
    return {
        "answer": rendered,
        "status": answer.status,
        "citations": list(citations.values()),
    }


def run_turn(
    messages: list[ChatMessage],
    *,
    client: OpenAI,
    data: CampusData,
    model: str,
    now: datetime,
) -> dict[str, Any]:
    started = monotonic()
    data.deadline = started + TURN_SECONDS
    history: list[Any] = [message.model_dump() for message in messages]
    evidence: dict[str, dict[str, Any]] = {}
    trace: list[dict[str, Any]] = []
    dataset_version: str | None = None
    week_start = now.date() - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=6)
    instructions = INSTRUCTIONS + (
        f"\nCurrent campus time: {now.isoformat()} (America/New_York).\n"
        f"Today is {now.strftime('%A, %B %d, %Y')}. "
        f"The current campus calendar week is {week_start} through {week_end}.\n"
    )
    tools = tool_definitions()
    for round_index in range(MAX_MODEL_CALLS):
        remaining = TURN_SECONDS - (monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("Turn deadline exceeded")
        final_round = round_index == MAX_MODEL_CALLS - 1 or len(trace) >= MAX_TOOL_CALLS
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=cast(ResponseInputParam, history),
            tools=tools,
            tool_choice="none" if final_round else "auto",
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
            timeout=min(30.0, remaining),
        )
        if response.status != "completed":
            raise InvalidAnswer("Incomplete model response")
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            try:
                result = render_answer(Answer.model_validate_json(response.output_text), evidence)
            except (ValidationError, InvalidAnswer) as error:
                if final_round:
                    raise InvalidAnswer("Invalid final answer") from error
                history.extend(response.output)
                history.append(
                    {
                        "role": "developer",
                        "content": "Your answer failed server validation: "
                        + str(error).split("\n")[0][:180]
                        + ". Return a corrected answer. Use only retrieved evidence, separate "
                        "limitations, and omit URLs. If evidence is missing, say so.",
                    }
                )
                continue
            return {
                **result,
                "model": response.model,
                "datasetVersion": dataset_version,
                "trace": trace,
                "elapsedMs": round((monotonic() - started) * 1000),
            }
        history.extend(response.output)
        for call in calls:
            tool_started = monotonic()
            arguments: dict[str, Any] = {}
            if len(trace) >= MAX_TOOL_CALLS or monotonic() - started >= TURN_SECONDS - 2:
                output: dict[str, Any] = {"status": "unavailable", "reason": "tool_budget"}
            else:
                try:
                    if call.name == "search_campus":
                        query = SearchQuery.model_validate_json(call.arguments)
                        arguments = query.model_dump(mode="json")
                        output = data.search(query)
                    elif call.name == "read_campus":
                        read = ReadQuery.model_validate_json(call.arguments)
                        arguments = read.model_dump(mode="json")
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
                evidence[record["id"]] = record
            trace.append(
                {
                    "tool": call.name,
                    "arguments": arguments,
                    "status": output["status"],
                    "result_count": len(output.get("records", [])),
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
    raise InvalidAnswer("Model did not finish within the tool budget")
