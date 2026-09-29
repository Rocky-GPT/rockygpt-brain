"""The Brain's web service, speaking the contract in rockygpt_brain/contract.py.

Milestone 1 builds the contract only, so every question gets the not_ready failure,
with the emergency help every failure carries.
"""

import hmac
import os
from collections.abc import Iterator
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from rockygpt_brain.contract import (
    EMERGENCY_TEXT,
    RETRYABLE,
    ChatRequest,
    EmergencyHelp,
    ErrorBody,
    ErrorCode,
    FailureReply,
    ProgressEvent,
    ResultEvent,
)

# The API description is for developers; production doesn't publish it.
app = FastAPI(
    title="RockyGPT Brain",
    openapi_url="/openapi.json" if os.getenv("BRAIN_ENVIRONMENT") == "development" else None,
)

FAILURE_MESSAGES: dict[str, str] = {
    "not_ready": "RockyGPT's new Brain can't answer questions yet.",
}


@app.get("/health")
@app.head("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


def failure(code: ErrorCode, request_id: str) -> FailureReply:
    return FailureReply(
        error=ErrorBody(
            code=code,
            message=FAILURE_MESSAGES[code],
            retryable=code in RETRYABLE,
            emergency=None if code == "request_cancelled" else EmergencyHelp(text=EMERGENCY_TEXT),
        ),
        reason=code,
        requestId=request_id,
    )


def wire(model: BaseModel) -> dict[str, Any]:
    """Unset optional fields are left out, as the apps expect."""
    return model.model_dump(mode="json", exclude_none=True)


def sse(name: str, model: BaseModel) -> str:
    return f"event: {name}\ndata: {model.model_dump_json(exclude_none=True)}\n\n"


@app.post("/v1/chat", response_model=None)
def chat(
    request: ChatRequest,
    x_rockygpt_environment_token: Annotated[str | None, Header()] = None,
    accept: Annotated[str | None, Header()] = None,
) -> JSONResponse | StreamingResponse:
    expected_token = os.getenv("STAGING_SERVICE_TOKEN", "").strip()
    if expected_token and not hmac.compare_digest(
        expected_token, x_rockygpt_environment_token or ""
    ):
        raise HTTPException(status_code=401, detail="Environment access token required")
    request_id = str(uuid4())
    status, body = 503, failure("not_ready", request_id)
    headers = {"X-Request-Id": request_id}
    if accept and "text/event-stream" in accept.lower():

        def events() -> Iterator[str]:
            yield sse("progress", ProgressEvent(stage="connecting"))
            yield sse("result", ResultEvent(status=status, body=body))

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={**headers, "Cache-Control": "no-cache, no-transform",
                     "X-Accel-Buffering": "no"},
        )
    return JSONResponse(status_code=status, content=wire(body), headers=headers)
