"""The Brain's web service: the contract in rockygpt_brain/contract.py over HTTP.

It reads the campus clock once per turn and hands the turn to rockygpt_brain/turn.py.
"""

import hmac
import os
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from rockygpt_brain.context import CAMPUS_TIMEZONE, read_context
from rockygpt_brain.contract import ChatRequest, ProgressEvent, ResultEvent
from rockygpt_brain.turn import answer_turn

# The API description is for developers; production doesn't publish it.
app = FastAPI(
    title="RockyGPT Brain",
    openapi_url="/openapi.json" if os.getenv("BRAIN_ENVIRONMENT") == "development" else None,
)


@app.get("/health")
@app.head("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


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
    context = read_context(request, datetime.now(CAMPUS_TIMEZONE))
    status, body = answer_turn(context, request_id)
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
