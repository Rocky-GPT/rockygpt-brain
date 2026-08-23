"""Application error hierarchy mapped 1:1 onto the ErrorResponse envelope
and status codes documented in spec/brain-api.openapi.yaml."""

from __future__ import annotations

from typing import Literal

ErrorCode = Literal[
    "INVALID_REQUEST",
    "PAYLOAD_TOO_LARGE",
    "UNAUTHORIZED",
    "RATE_LIMITED",
    "DATASET_UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "INTERNAL_ERROR",
    "NOT_FOUND",
]


class AppError(Exception):
    status_code: int = 500
    code: ErrorCode = "INTERNAL_ERROR"
    retryable: bool = False

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds


class InvalidRequestError(AppError):
    status_code = 400
    code = "INVALID_REQUEST"
    retryable = False


class UnauthorizedError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    retryable = False


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    retryable = False


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"
    retryable = False


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    retryable = True

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message, retry_after_seconds=retry_after_seconds)


class DatasetUnavailableError(AppError):
    status_code = 503
    code = "DATASET_UNAVAILABLE"
    retryable = True


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    retryable = True


class InternalError(AppError):
    status_code = 500
    code = "INTERNAL_ERROR"
    retryable = False
