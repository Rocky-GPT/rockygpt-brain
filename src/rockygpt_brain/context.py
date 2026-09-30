"""Step 3, context: what the conversation so far means for the current turn.

A pure function of the request, so the same conversation always gives the same Context. The
latest message is kept apart from the older ones, which stay in the order they were said.
Jev reads `recent_messages` later; that is how "their" in "What's their phone number?" can
still be understood as Financial Aid. The topic and entities are only a first guess from the
student's own words and the small table below, a stand-in for what Jev and the campus data will
say. Messages the app left out (`omittedMessages`) are not counted here.
"""

import re
from dataclasses import dataclass

from rockygpt_brain.boundary import plain
from rockygpt_brain.contract import ChatMessage, ChatRequest

MAX_RECENT_MESSAGES = 8

# (topic, the office it names if it names one, words that mention it; a trailing "s" also counts)
_TABLE = (
    ("shuttle", None, ("shuttle", "bus", "buses")),
    ("financial aid", "Financial Aid", ("financial aid", "fafsa")),
    ("registrar", "Registrar", ("registrar",)),
)
_ROWS = [(topic, entity, re.compile(rf"\b(?:{'|'.join(words)})s?\b"))
         for topic, entity, words in _TABLE]


@dataclass(frozen=True, slots=True)
class Context:
    latest_message: str
    recent_messages: tuple[ChatMessage, ...]  # older than the latest, oldest first
    current_topic: str | None
    referenced_entities: tuple[str, ...]  # oldest mention first
    history_available: bool  # whether recent_messages holds anything


def _mentioned(message: ChatMessage) -> list[tuple[str, str | None]]:
    """The table rows the student's words name. What RockyGPT said never counts."""
    if message.role != "user":
        return []
    text = plain(message.content)
    return [(topic, entity) for topic, entity, pattern in _ROWS if pattern.search(text)]


def build_context(request: ChatRequest) -> Context:
    *earlier, latest = request.messages
    recent = tuple(earlier[-MAX_RECENT_MESSAGES:])
    mentions = [_mentioned(message) for message in (*recent, latest)]
    topic = next((hits[0][0] for hits in reversed(mentions) if hits), None)
    entities = dict.fromkeys(
        entity for hits in mentions for _, entity in hits if entity is not None
    )
    return Context(
        latest_message=latest.content,
        recent_messages=recent,
        current_topic=topic,
        referenced_entities=tuple(entities),
        history_available=bool(recent),
    )
