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
from rockygpt_brain.brain.tools.validation import validate_arguments
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
        return {"error": "invalid_arguments"}
    result = await tool.handler(client, time_context, validated_arguments)
    return summarize(result, registry=registry)
