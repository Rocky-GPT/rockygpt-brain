"""Shared-secret gate for functional staging routes.

See spec/system-boundaries.md: "When STAGING_SERVICE_TOKEN is configured,
all functional brain requests must require this header." Probes are always
public.
"""

from __future__ import annotations

import hmac

# A header name, not a credential value.
ENVIRONMENT_TOKEN_HEADER = "x-rockygpt-environment-token"  # noqa: S105

PUBLIC_PATHS = frozenset({"/health", "/readiness"})


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


def token_is_valid(*, presented: str | None, expected: str) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
