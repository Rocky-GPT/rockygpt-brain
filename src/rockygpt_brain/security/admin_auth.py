"""Bearer authentication for /v1/admin/* routes.

Enforced independently of the environment-token gate: admin routes require
both (when a staging token is configured) per
spec/brain-api.openapi.yaml's per-operation `security: [AdminBearer]` plus
the shared `EnvironmentToken` parameter.
"""

from __future__ import annotations

import hmac
import re

MAX_HEADER_LENGTH = 4096
MAX_TOKEN_LENGTH = 2048

# RFC 7235: auth-scheme is case-insensitive. Exactly one scheme token
# followed by exactly one credential token68 (no extra fields/params).
_BEARER_PATTERN = re.compile(r"^bearer[ \t]+(\S+)$", re.IGNORECASE)


def extract_bearer_token(authorization_header: str | None) -> str | None:
    if not authorization_header or len(authorization_header) > MAX_HEADER_LENGTH:
        return None
    match = _BEARER_PATTERN.match(authorization_header.strip())
    if not match:
        return None
    token = match.group(1)
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    return token


def token_is_valid(*, presented: str | None, expected: str) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
