from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rockygpt_brain.safety.responses import CONCERNS
from rockygpt_brain.safety.schema import Concern


def required(concerns: Iterable[Concern]) -> list[dict[str, Any]]:
    return [{"concern": c.value, "must": CONCERNS[c]} for c in concerns]
