"""PYTHON: run the lane.

The stage between the two brains. A checked plan goes in, whatever the lane
produced comes out.

No lane has an executor yet, so every plan returns the same empty result. That
is deliberately visible rather than hidden: the trace says which lane would
have run and that nothing did, so a turn answered from the model's own
knowledge cannot be mistaken for one answered from campus data.

A lane earns its executor by growing a branch here. Nothing else moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rockygpt_brain.core.plan import Plan
from rockygpt_brain.core.validate import Rejected


@dataclass(frozen=True, slots=True)
class Execution:
    lane: str
    ran: bool
    note: str
    results: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"lane": self.lane, "ran": self.ran}
        if self.results:
            out["results"] = self.results
        if self.note:
            out["note"] = self.note
        return out


def run(checked: Plan | Rejected) -> Execution:
    """Act on a checked plan. Nothing runs while no lane has an executor."""
    if isinstance(checked, Rejected):
        return Execution(lane="none", ran=False, note=checked.reason)
    return Execution(
        lane=checked.lane.value,
        ran=False,
        note=f"no executor for the {checked.lane.value} lane yet",
    )
