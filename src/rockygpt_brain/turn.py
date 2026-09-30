"""Step 1, turn intake: one validated request becomes one `Turn`.

The campus clock is read once here and carried on the Turn, so one turn never sees two times.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from rockygpt_brain.contract import ChatRequest

CAMPUS_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class Turn:
    message: str
    conversation_id: str
    request_id: str
    campus_now: datetime


def new_id() -> str:
    return str(uuid.uuid4())


def intake(request: ChatRequest, now: datetime | None = None) -> Turn:
    """`now` is for tests and must carry a time zone."""
    now = datetime.now(CAMPUS_TZ) if now is None else now.astimezone(CAMPUS_TZ)
    return Turn(
        message=request.messages[-1].content,
        conversation_id=new_id(),  # the apps send none yet, so every turn starts a conversation
        request_id=new_id(),
        campus_now=now.replace(microsecond=0),
    )
