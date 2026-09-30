"""Step 5, the code plan: what the Brain does with this turn, decided by code alone.

Jev has said what the student needs (`Understanding`) and the conversation has said what
history exists (`Context`). Both are only read here. The rules run in order and the first one
that matches decides the path, so the same Understanding and Context always give the same Plan.
"""

from dataclasses import dataclass
from typing import Literal

from rockygpt_brain.context import Context
from rockygpt_brain.understanding import Understanding

Path = Literal[
    "safety", "capability_limit", "multi_part", "clarify",
    "campus", "conversation", "general",
]

_BY_NEEDS: dict[str, Path] = {
    "unclear": "clarify", "campus_info": "campus", "conversation": "conversation",
    "outside": "general",
}


@dataclass(frozen=True)
class Plan:
    path: Path
    topic: str
    uses_history: bool


def build_plan(understanding: Understanding, context: Context) -> Plan:
    if understanding.danger:
        path: Path = "safety"
    elif understanding.needs == "own_account":
        path = "capability_limit"
    elif understanding.multi_part:
        path = "multi_part"
    elif understanding.needs_history and not context.history_available:
        path = "clarify"
    else:
        path = _BY_NEEDS[understanding.needs]
    uses_history = understanding.needs_history and context.history_available
    return Plan(path, understanding.topic, uses_history)
