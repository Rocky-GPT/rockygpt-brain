"""The Brain's web service.

`/health` and `/readiness` are the probes the apps and run-local.sh look at. `/v1/chat` takes
the turn in (step 1, `turn.py`); nothing answers it yet, so a valid question gets `not_ready`.
"""

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from rockygpt_brain.contract import ChatRequest
from rockygpt_brain.failures import failure
from rockygpt_brain.turn import intake, new_id

app = FastAPI(title="RockyGPT Brain")
INVALID = "The request isn't in the shape RockyGPT expects."


@app.exception_handler(RequestValidationError)
def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Refuse a request that doesn't fit, without repeating anything the student wrote.

    `detail` keeps FastAPI's `type` and `loc` (the student app reads them) and drops `input`.
    """
    detail = [
        {"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()
    ]
    return failure(422, "invalid_request", INVALID, new_id(), detail=detail)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    """FastAPI answers a body it can't parse at all (bad UTF-8, absurd nesting) with a bare 400."""
    if exc.status_code == 400:
        detail = [{"type": "body_unreadable", "loc": ["body"], "msg": "The body can't be read."}]
        return failure(422, "invalid_request", INVALID, new_id(), detail=detail)
    return await http_exception_handler(request, exc)  # type: ignore[return-value]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readiness")
def readiness() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/v1/chat")
def chat(request: ChatRequest) -> JSONResponse:
    turn = intake(request)
    return failure(503, "not_ready", "RockyGPT can't answer questions yet.", turn.request_id)
