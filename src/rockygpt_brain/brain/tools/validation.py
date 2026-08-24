"""Server-side re-validation of model-supplied tool arguments.

Arguments the model supplies are never trusted at face value. Everything
here checks against the same JSON-schema `properties` used to advertise the
tool in `specs.py`, so the contract the model was shown and the contract
enforced on its output cannot diverge.
"""

from __future__ import annotations

from typing import Any

from rockygpt_brain.brain.tools.payload import ToolDefinition
from rockygpt_brain.brain.tools.specs import TOOL_HANDLERS


def validate_arguments(tool: ToolDefinition, arguments: Any) -> dict[str, str] | None:
    """Re-validate model-supplied arguments against the tool's own schema.

    The JSON schema in `tool.parameters` is advertised to the model, but the
    model's output is never trusted at face value: this enforces the same
    bounds (object type, exact allowed keys, string type, length, enum)
    server-side before anything reaches the data client.
    """
    if not isinstance(arguments, dict):
        return None
    properties: dict[str, Any] = tool.parameters.get("properties", {})
    if not set(arguments.keys()) <= set(properties.keys()):
        return None
    validated: dict[str, str] = {}
    for key, value in arguments.items():
        spec = properties[key]
        if spec.get("type") != "string" or not isinstance(value, str):
            return None
        max_length = spec.get("maxLength")
        if max_length is not None and len(value) > max_length:
            return None
        enum = spec.get("enum")
        if enum is not None and value not in enum:
            return None
        validated[key] = value
    return validated


def declared_argument_keys(name: str, arguments: Any) -> list[str]:
    """Which of a tool's *declared* argument names the model actually supplied.

    Names only — never values. Whether the model narrowed a search or left an
    optional filter off is the difference between "the evidence was never
    retrievable" and "the evidence was there and the wrong record was picked",
    and that distinction is not recoverable from the answer text alone.

    Filtered through the tool's own schema `properties` for the same reason
    `_tool_log_name` filters the tool name: a model-invented key is not a fact
    about this system and must not be retained verbatim in anything that
    reaches a log. Sorted so a diagnostic comparison is order-insensitive.
    """
    tool = TOOL_HANDLERS.get(name)
    if tool is None or not isinstance(arguments, dict):
        return []
    properties: dict[str, Any] = tool.parameters.get("properties", {})
    return sorted(key for key in arguments if key in properties)
