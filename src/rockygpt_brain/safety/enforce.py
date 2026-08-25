"""Acting on the concerns a plan carries, before any lane runs.

Every concern is acted on, not the first — a question can ask for a password
on behalf of someone in trouble, and both halves need answering.

This depends on nothing that can fail: no capability, no executor, no network.
That is the point of doing it before the lane, since the turns that most need
an answer are the ones least able to wait for campus data to come back.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rockygpt_brain.safety.responses import CONCERNS
from rockygpt_brain.safety.schema import Concern


def required(concerns: Iterable[Concern]) -> list[dict[str, Any]]:
    """One row per concern: what it is, and what the answer must do about it."""
    return [{"concern": c.value, "must": CONCERNS[c]} for c in concerns]
