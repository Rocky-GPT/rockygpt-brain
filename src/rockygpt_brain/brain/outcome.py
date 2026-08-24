"""The settled result of one chat turn.

Every path through the pipeline — a grounded answer, a safety
short-circuit, a fallback — ends in exactly this shape, which is what lets
`run_chat_turn` promise it never returns anything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rockygpt_brain.schemas.common import Citation, UiAction


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
