"""Request ID generation.

Every response carries a stable request ID in both the body and the
`X-Request-Id` header (spec/brain-api.openapi.yaml). For /v1/chat, this ID
*is* the ChatSuccess.requestId used later for feedback upserts.
"""

from __future__ import annotations

import uuid


def new_request_id() -> str:
    return uuid.uuid4().hex
