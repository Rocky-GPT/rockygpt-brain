"""Bounded conversation data, never an authority for campus facts.

Keep the latest message intact and a contiguous suffix of earlier messages. Both the
client's omissions and our own omissions travel with the context. There is no topic table
or classifier deciding whether the assistant may read earlier conversation.
"""

from dataclasses import dataclass

from rockygpt_brain.contract import ChatMessage, ChatRequest

MAX_HISTORY_BYTES = 32_000


@dataclass(frozen=True, slots=True)
class Context:
    latest_message: str
    recent_messages: tuple[ChatMessage, ...]
    client_omitted_messages: int
    server_omitted_messages: int

    @property
    def omitted_messages(self) -> int:
        return self.client_omitted_messages + self.server_omitted_messages


def build_context(request: ChatRequest, *, history_bytes: int = MAX_HISTORY_BYTES) -> Context:
    if history_bytes < 0:
        raise ValueError("history_bytes must be nonnegative")
    *earlier, latest = request.messages
    kept: list[ChatMessage] = []
    remaining = min(history_bytes, max(0, 48_000 - len(latest.content.encode("utf-8"))))
    for message in reversed(earlier):
        size = len(message.content.encode("utf-8"))
        if size > remaining:
            break
        kept.append(message)
        remaining -= size
    kept.reverse()
    return Context(latest.content, tuple(kept), request.omittedMessages, len(earlier) - len(kept))
