"""Keyed, non-reversible transformation for durable identifiers.

Per spec/acceptance.md: "Durable identifiers use a keyed, non-reversible
transformation." conversationId and visitorId must never be stored raw
(THREAT_MODEL.md §3.3); this module is the only place that turns a raw
identifier into the value that persistence/chat_logs.py writes.
"""

from __future__ import annotations

import hmac
from hashlib import sha256


def hash_identifier(*, hash_key: str, value: str) -> str:
    return hmac.new(hash_key.encode("utf-8"), value.encode("utf-8"), sha256).hexdigest()
