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
