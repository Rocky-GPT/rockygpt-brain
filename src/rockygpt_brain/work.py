"""Development diagnostics: which Brain answered, and who did the work in each step.

The dev UI's Timeline and its JSON export read this as `diagnostics` (rockygpt-dev
lib/chat-stream.ts `workedSteps`): each step with when it began, and each Jev call
with the step it ran in. Times are milliseconds from when the request arrived. Time
in a step outside its calls is the Brain's own code, the ledger's bookkeeping included.
It holds no student words.
"""

import os
import subprocess
from functools import cache
from pathlib import Path
from time import monotonic
from typing import Any

from rockygpt_brain.contract import ProgressEvent


@cache
def revision() -> str | None:
    """The commit this Brain runs. The deploy sets BRAIN_REVISION (Render sets
    RENDER_GIT_COMMIT); a Brain run from a checkout reads it from git, marked
    "-dirty" when the checkout has changes. Tags are left out: the repo's old
    v1.0.0-pre-rewrite tag made the new Brain read as "pre-rewrite" (09-29)."""
    named = os.getenv("BRAIN_REVISION") or os.getenv("RENDER_GIT_COMMIT")
    if named:
        return named
    try:
        found = subprocess.run(  # noqa: S603
            ["git", "describe", "--always", "--dirty", "--abbrev=40", "--exclude=*"],  # noqa: S607
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return found.stdout.strip() or None


class Work:
    def __init__(self, started: float) -> None:
        self.started = started
        self.steps: list[dict[str, Any]] = [{"stage": "connecting", "subjects": [], "atMs": 0}]
        self.calls: list[dict[str, Any]] = []

    def ms(self, at: float) -> int:
        return round((at - self.started) * 1000)

    def step(self, event: ProgressEvent) -> None:
        """A progress event. Safety help sent within a step belongs to that step."""
        if event.safety is not None and event.stage == self.steps[-1]["stage"]:
            self.steps[-1]["safety"] = True
            return
        self.steps.append({"stage": event.stage,
                           "subjects": [subject.model_dump(exclude_none=True)
                                        for subject in event.subjects],
                           "atMs": self.ms(monotonic()),
                           **({"safety": True} if event.safety is not None else {})})

    def decided(self, **facts: Any) -> None:
        """What the step now running decided, e.g. written={"by": "code", ...}."""
        self.steps[-1].update(facts)

    def jev_call(self, sent: float, returned: float, failed: bool) -> None:
        self.calls.append({"who": "jev", "what": "routing", "step": len(self.steps) - 1,
                           "startMs": self.ms(sent), "ms": round((returned - sent) * 1000),
                           **({"failed": True} if failed else {})})

    def report(self) -> dict[str, Any]:
        return {"steps": list(self.steps), "calls": list(self.calls),
                "endMs": self.ms(monotonic())}
