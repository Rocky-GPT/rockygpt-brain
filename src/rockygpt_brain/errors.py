from __future__ import annotations

from typing import ClassVar

from rockygpt_brain.api.contracts import ErrorCode


class ServiceError(Exception):
    status_code: ClassVar[int]
    code: ClassVar[ErrorCode]
    retryable: ClassVar[bool]

    def __init__(self, public_message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.retry_after_seconds = retry_after_seconds

    def __str__(self) -> str:
        return self.public_message


class BadRequest(ServiceError):
    status_code = 400
    code: ClassVar[ErrorCode] = "INVALID_REQUEST"
    retryable = False


class Unauthorized(ServiceError):
    status_code = 401
    code: ClassVar[ErrorCode] = "UNAUTHORIZED"
    retryable = False


class Internal(ServiceError):
    status_code = 500
    code: ClassVar[ErrorCode] = "INTERNAL_ERROR"
    retryable = False


class Unavailable(ServiceError):
    status_code = 503
    code: ClassVar[ErrorCode] = "SERVICE_UNAVAILABLE"
    retryable = True


class DatasetUnavailable(Unavailable):
    code: ClassVar[ErrorCode] = "DATASET_UNAVAILABLE"


class Unsupported(ServiceError):
    status_code = 503
    code: ClassVar[ErrorCode] = "SERVICE_UNAVAILABLE"
    retryable = False
