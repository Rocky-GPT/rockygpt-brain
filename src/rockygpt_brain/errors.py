"""Errors that can be returned by the public API."""

from __future__ import annotations

from dataclasses import dataclass

from rockygpt_brain.contracts import ErrorCode


@dataclass(slots=True)
class ServiceError(Exception):
    status_code: int
    code: ErrorCode
    public_message: str
    retryable: bool = False
    retry_after_seconds: int | None = None

    def __str__(self) -> str:
        return self.public_message
