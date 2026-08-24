"""Where a conversation's discourse record lives between turns.

The client sends at most ten raw history entries and the browser fills them
walking backwards, so a fact five exchanges old is not in the request at all —
`dsc-topic-shift-recall` measured 0%, every run, because the exchange had been
evicted by menu listings and club rosters before the question was asked. No
prompt reaches that: the information is absent before inference starts.

Holding the record server-side is what makes it survive. It is a small,
bounded, in-process store — not Redis and not Postgres, because losing it costs
nothing but the ability to answer "what did you tell me" for conversations in
flight, which is exactly the behaviour that existed before this module.

Scoped to a visitor *and* a conversation. A conversation id is only a value the
client sends, so keying on it alone would hand one visitor's record to anyone
who repeated the id. A turn without both ids is answered normally and simply
not retained — degrading to prior behaviour rather than sharing a record.
"""

from __future__ import annotations

from collections import OrderedDict

from rockygpt_brain.brain.discourse import DiscourseRecord

# Conversations tracked at once; the least recently used is dropped first. A
# bound on a process-lifetime store is a memory guarantee, not a policy choice
# (THREAT_MODEL.md 3.7).
MAX_CONVERSATIONS = 200
# Caps how much of a client-supplied identifier can reach a key.
MAX_ID_CHARS = 128

_records: OrderedDict[str, DiscourseRecord] = OrderedDict()


def _key(visitor_id: str | None, conversation_id: str | None) -> str | None:
    if not visitor_id or not conversation_id:
        return None
    return f"{visitor_id[:MAX_ID_CHARS]}\x1f{conversation_id[:MAX_ID_CHARS]}"


def record_for(visitor_id: str | None, conversation_id: str | None) -> DiscourseRecord | None:
    """The record for this conversation, created on first use.

    `None` when the turn cannot be scoped to both a visitor and a conversation.
    Callers treat that as "no record this turn" and answer without one.
    """
    key = _key(visitor_id, conversation_id)
    if key is None:
        return None

    existing = _records.get(key)
    if existing is not None:
        _records.move_to_end(key)
        return existing

    created = DiscourseRecord()
    _records[key] = created
    while len(_records) > MAX_CONVERSATIONS:
        _records.popitem(last=False)
    return created


def reset() -> None:
    """Drop every record. For tests; never called by the service."""
    _records.clear()
