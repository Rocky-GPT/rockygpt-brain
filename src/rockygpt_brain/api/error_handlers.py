"""Exception -> HTTP error envelope.

Every error the service returns is built here, so the `ErrorResponse` shape
in spec/brain-api.openapi.yaml has exactly one producer. Nothing in this
module reflects an exception's own text back to the caller: messages are
fixed literals chosen per class of failure, which is what keeps an internal
detail (a driver error, a stack frame, a connection string) out of a
response body.

Each handler re-checks its exception type at runtime. Starlette's
`add_exception_handler` requires a `Callable[[Request, Exception], ...]`
signature — it is contravariant in the exception type, so a handler
narrowed to a subclass isn't a valid substitute from a type-checker's
perspective, even though Starlette only ever calls a handler registered for
`AppError` with an `AppError`. `isinstance` + `raise` (not `assert`, which
`-O` can strip) both narrows for mypy and is a real, always-on check that
the handler was wired to the exception type it expects.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from rockygpt_brain.errors import AppError, ErrorCode
from rockygpt_brain.observability.request_id import new_request_id
from rockygpt_brain.schemas.common import ErrorDetail, ErrorResponse

# Deliberately the app logger rather than this module's own name: operator
# log filters for unhandled exceptions predate this module and should keep
# matching after the split.
logger = logging.getLogger("rockygpt_brain.app")

_HTTP_STATUS_CODES: dict[int, ErrorCode] = {
    401: "UNAUTHORIZED",
    404: "NOT_FOUND",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_request_id()


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        request_id=_request_id(request),
        error=ErrorDetail(
            code=code,
            message=message,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
        ),
    )
    headers = {"Retry-After": str(retry_after_seconds)} if retry_after_seconds else None
    return JSONResponse(
        status_code=status_code, content=body.model_dump(by_alias=True), headers=headers
    )


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise TypeError("_handle_app_error registered for a non-AppError exception type")
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        retry_after_seconds=exc.retry_after_seconds,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise TypeError(
            "_handle_validation_error registered for a non-RequestValidationError type"
        )
    return _error_response(
        request,
        status_code=400,
        code="INVALID_REQUEST",
        message="The request is malformed or violates a documented bound.",
        retryable=False,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError("_handle_http_exception registered for a non-HTTPException type")
    code = _HTTP_STATUS_CODES.get(
        exc.status_code, "INVALID_REQUEST" if exc.status_code < 500 else "INTERNAL_ERROR"
    )
    return _error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message="The request could not be processed.",
        retryable=exc.status_code >= 500,
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception", extra={"route": request.url.path})
    return _error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected service error occurred.",
        retryable=False,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire every handler above onto the app, in registration order."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)
