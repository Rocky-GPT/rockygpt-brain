"""Model-callable tools, one per rockygpt-data search/lookup endpoint.

Arguments the model supplies are never trusted at face value: `execute_tool`
re-validates every call against the same JSON-schema `properties` used to
advertise the tool to the model (exact allowed keys, string type, length
bound, enum membership — see `validation.validate_arguments`), rejecting
anything else with a small, fixed, non-reflective error rather than passing
attacker- or model-controlled values straight to the data client.

Only records that survive both the per-call cap (`MAX_RECORDS_PER_CALL`) and
the final total-size cap (`MAX_TOTAL_SERIALIZED_BYTES`, measured on the
*complete* returned tool object — dataset fields and all — as UTF-8 bytes of
its JSON serialization) have their `source` recorded into the turn's
`ProvenanceRegistry`. Sizing and dropping happens strictly before
registration, so a record trimmed for size is never citable even though it
was briefly present in an intermediate, unsent representation (DESIGN.md
§4). Every field is also recursively bounded (string length, list length,
nesting depth, non-finite floats rejected) before that size check runs, so
a large or malformed data-service response cannot exhaust model context or
produce invalid JSON.

`at` (the pinned-time parameter accepted by rockygpt-data's hours/shuttle/
menu endpoints) is never a model-controlled parameter: it is always injected
server-side from the turn's `TimeContext` (spec/acceptance.md: "Pinned now
and timezone values control hours and shuttle calculations").

Layout:

- `payload.py`    the shapes a call passes around (`ToolPayload`, `ToolDefinition`)
- `handlers.py`   one adapter per data endpoint
- `specs.py`      the tool catalogue and its argument schemas
- `validation.py` server-side re-validation of model-supplied arguments
- `bounding.py`   size bounds, trimming, and provenance registration
- `dispatch.py`   `execute_tool`, the orchestrator's single entry point
"""

from __future__ import annotations

from rockygpt_brain.brain.tools.bounding import (
    MAX_DEPTH,
    MAX_LIST_ITEMS,
    MAX_RECORDS_PER_CALL,
    MAX_STRING_LENGTH,
    MAX_TOTAL_SERIALIZED_BYTES,
    summarize,
)
from rockygpt_brain.brain.tools.dispatch import execute_tool
from rockygpt_brain.brain.tools.payload import ToolDefinition, ToolPayload
from rockygpt_brain.brain.tools.specs import (
    TOOL_DEFINITIONS,
    TOOL_HANDLERS,
    openai_tool_specs,
)
from rockygpt_brain.brain.tools.validation import declared_argument_keys, validate_arguments

__all__ = [
    "MAX_DEPTH",
    "MAX_LIST_ITEMS",
    "MAX_RECORDS_PER_CALL",
    "MAX_STRING_LENGTH",
    "MAX_TOTAL_SERIALIZED_BYTES",
    "TOOL_DEFINITIONS",
    "TOOL_HANDLERS",
    "ToolDefinition",
    "ToolPayload",
    "declared_argument_keys",
    "execute_tool",
    "openai_tool_specs",
    "summarize",
    "validate_arguments",
]
