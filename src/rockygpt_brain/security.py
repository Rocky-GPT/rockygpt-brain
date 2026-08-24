"""Deterministic authentication, pseudonymization, rate bounds, and redaction."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from rockygpt_brain.errors import ServiceError


def require_shared_token(provided: str | None, configured: str | None) -> None:
    if configured is not None and (
        provided is None or not secrets.compare_digest(provided, configured)
    ):
        raise ServiceError(401, "UNAUTHORIZED", "The environment credential is invalid.")


def require_admin_bearer(authorization: str | None, configured: str | None) -> None:
    if configured is None:
        raise ServiceError(404, "NOT_FOUND", "The requested resource was not found.")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or not secrets.compare_digest(token, configured):
        raise ServiceError(401, "UNAUTHORIZED", "Administrator authentication is required.")


def verify_signed_client(client_key: str | None, signature: str | None, secret: str | None) -> bool:
    if not client_key or not signature or not secret or len(signature) != 64:
        return False
    try:
        bytes.fromhex(signature)
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), client_key.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), signature.lower())


def pseudonymize(value: str | None, secret: str | None, namespace: str) -> str:
    if not value:
        return f"anonymous:{namespace}"
    if not secret:
        # Development-only stable process-safe fallback; production validates a configured key.
        return f"dev:{namespace}:" + hashlib.sha256(value.encode()).hexdigest()
    digest = hmac.new(secret.encode(), f"{namespace}:{value}".encode(), hashlib.sha256).hexdigest()
    return f"hmac:{namespace}:{digest}"


_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "[EMAIL]"),
    (re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"), "[PHONE]"),
    (re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), "[SSN]"),
    (re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)"), "[PAYMENT_NUMBER]"),
    (re.compile(r"\b(?:R|A)\d{8}\b", re.I), "[STUDENT_ID]"),
    (
        re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b"),
        "[SECRET]",
    ),
)


def redact_text(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = value
    for pattern, replacement in _REDACTIONS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


@dataclass(frozen=True, slots=True)
class ClientIdentity:
    rate_key: str
    trusted: bool


def client_identity(client_key: str | None, signature: str | None, abuse_secret: str | None) -> ClientIdentity:
    trusted = verify_signed_client(client_key, signature, abuse_secret)
    if trusted and client_key:
        # This digest is process-only and is never persisted.
        return ClientIdentity("trusted:" + hashlib.sha256(client_key.encode()).hexdigest(), True)
    return ClientIdentity("untrusted", False)


class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = float(window_seconds)
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        now = time.monotonic()
        async with self._lock:
            events = self._events[key]
            while events and now - events[0] >= self._window:
                events.popleft()
            if len(events) >= self._limit:
                retry = max(1, int(self._window - (now - events[0])) + 1)
                raise ServiceError(
                    429,
                    "RATE_LIMITED",
                    "Too many requests. Please try again shortly.",
                    retryable=True,
                    retry_after_seconds=retry,
                )
            events.append(now)
