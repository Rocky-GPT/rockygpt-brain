"""Name -> tool lookup, argument validation, execution, summarization.

The one entry point the orchestrator calls. Failures are reported as small,
fixed, non-reflective error objects rather than by raising, so a bad model
call costs one wasted tool slot instead of the whole turn.
"""

from __future__ import annotations

from typing import Any

from rockygpt_brain.brain.grounding import ProvenanceRegistry
from rockygpt_brain.brain.time_context import TimeContext
from rockygpt_brain.brain.tools.bounding import summarize
from rockygpt_brain.brain.tools.specs import TOOL_HANDLERS
from rockygpt_brain.brain.tools.validation import classify_argument_defect, validate_arguments
from rockygpt_brain.data_client.client import DataServiceClient


async def execute_tool(
    name: str,
    arguments: Any,
    *,
    client: DataServiceClient,
    time_context: TimeContext,
    registry: ProvenanceRegistry,
) -> dict[str, Any]:
    tool = TOOL_HANDLERS.get(name)
    if tool is None:
        return {"error": "unknown_tool"}
    validated_arguments = validate_arguments(tool, arguments)
    if validated_arguments is None:
        # `_defect` names the rule that rejected the call, from the fixed
        # vocabulary in validation.py. The leading underscore marks it as
        # operator-only: orchestrator.py records it in the tool-call log and
        # strips it before the result reaches the model, which continues to see
        # the same small, fixed, non-reflective error it always has.
        defect = classify_argument_defect(tool, arguments)
        error: dict[str, Any] = {"error": "invalid_arguments"}
        if defect is not None:
            error["_defect"] = defect
        return error
    result = await tool.handler(client, time_context, validated_arguments)
    return summarize(result, registry=registry)
