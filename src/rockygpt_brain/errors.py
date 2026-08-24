"""Service-safe failures and their frozen external mapping."""

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


class ModelUnavailableError(RuntimeError):
    """The configured model cannot currently complete the request."""


class ModelOutputError(RuntimeError):
    """The model did not return the required structured output."""


class DataUnavailableError(RuntimeError):
    """DATA could not authoritatively answer the request."""


class GroundingError(RuntimeError):
    """A draft references absent or incompatible evidence."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons
