"""Conversation context: the conversation read once, for every later step.

Code keeps the conversation in order and knows what is missing from it. Working out
what the student's words refer to ("what about tomorrow?", "that one") is Jev's job
(milestone 4), so nothing here reads the words themselves.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from rockygpt_brain.contract import ChatMessage, ChatRequest

CAMPUS_TIMEZONE = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Context:
    """What every later step knows about the turn. Nothing reads the request again."""

    question: str
    earlier: tuple[ChatMessage, ...]
    omitted: int
    # The turn's one campus clock, read once when the question arrived.
    now: datetime

    @property
    def history_complete(self) -> bool:
        """False when the app left earlier messages out. What they said is unknown, so
        no step may say something wasn't said before."""
        return self.omitted == 0

    @property
    def first_question(self) -> bool:
        """Nothing came before this question, sent or left out."""
        return not self.earlier and self.history_complete

    @property
    def previous_answer(self) -> str | None:
        """The Brain's last reply, which a follow-up most often leans on."""
        return next((message.content for message in reversed(self.earlier)
                     if message.role == "assistant"), None)

    @property
    def message_number(self) -> int:
        """Where the new question sits in the whole conversation, counting from 1."""
        return self.omitted + len(self.earlier) + 1


def read_context(request: ChatRequest, now: datetime) -> Context:
    if now.tzinfo is None:
        raise ValueError("The campus clock needs a time zone")
    *earlier, question = request.messages
    return Context(
        question=question.content,
        earlier=tuple(earlier),
        omitted=request.omittedMessages,
        now=now.astimezone(CAMPUS_TIMEZONE),
    )
