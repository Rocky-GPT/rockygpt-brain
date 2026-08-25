"""One recorded turn.

`prompt_value` is the shape a later turn is shown. It is deliberately smaller
than what is stored: a follow-up needs what was said, not the request id, the
latency, or which tools ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Turn:
    request_id: str
    user: str
    assistant: str
    route: str
    created_at: datetime

    def prompt_value(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "user": self.user,
            "assistant": self.assistant,
            "route": self.route,
            "createdAt": self.created_at.isoformat(),
        }
