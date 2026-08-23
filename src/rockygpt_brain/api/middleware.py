"""Cross-cutting request middleware: request-ID assignment and the shared
environment-token gate (spec/system-boundaries.md's "Shared security
boundary": every functional route requires
`x-rockygpt-environment-token` when `STAGING_SERVICE_TOKEN` is configured;
`/health` and `/readiness` stay public)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from rockygpt_brain.config import Settings
from rockygpt_brain.observability.request_id import new_request_id
from rockygpt_brain.schemas.common import ErrorDetail, ErrorResponse
from rockygpt_brain.security.environment_token import (
    ENVIRONMENT_TOKEN_HEADER,
    is_public_path,
    token_is_valid,
)

REQUEST_ID_HEADER = "X-Request-Id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = new_request_id()
        request.state.request_id = request_id

        if self._settings.environment_token_required and not is_public_path(request.url.path):
            presented = request.headers.get(ENVIRONMENT_TOKEN_HEADER)
            expected = self._settings.staging_service_token
            expected_value = expected.get_secret_value() if expected else ""
            if not token_is_valid(presented=presented, expected=expected_value):
                body = ErrorResponse(
                    request_id=request_id,
                    error=ErrorDetail(
                        code="UNAUTHORIZED",
                        message="A valid x-rockygpt-environment-token header is required.",
                        retryable=False,
                    ),
                )
                response: Response = JSONResponse(
                    status_code=401, content=body.model_dump(by_alias=True)
                )
                response.headers[REQUEST_ID_HEADER] = request_id
                return response

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
