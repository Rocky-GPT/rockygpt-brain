"""One shape for every failure the Brain sends (docs/contract.md, "A failure")."""

from typing import Any

from fastapi.responses import JSONResponse


def failure(
    status: int, code: str, message: str, request_id: str, *, retryable: bool = False,
    **extra: Any,
) -> JSONResponse:
    body = {
        "error": {"code": code, "message": message, "retryable": retryable},
        "reason": code,
        "requestId": request_id,
        **extra,
    }
    return JSONResponse(body, status_code=status,
                        headers={"X-Request-Id": request_id, "Cache-Control": "no-store"})
