"""The Brain's web service: the contract in rockygpt_brain/contract.py over HTTP.

It reads the campus clock once per turn and hands the turn to rockygpt_brain/turn.py.

Settings, all server-side:
- BRAIN_ENVIRONMENT: development or production. Picks the spending allowance, and
  development alone publishes the API description and answers diagnostics.
- BRAIN_LEDGER_DATABASE_URL: the spending ledger (spending.py).
- BRAIN_TYPESAFE_API_KEY: Jev. Without it, or without the two above, Jev is skipped.
- STAGING_SERVICE_TOKEN: when set, every chat must carry it.
"""

import hmac
import json
import logging
import os
import traceback
from collections.abc import Iterator
from datetime import datetime
from functools import cache
from time import monotonic
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from rockygpt_brain.context import CAMPUS_TIMEZONE, Context, read_context
from rockygpt_brain.contract import ChatReply, ChatRequest, ProgressEvent, ResultEvent
from rockygpt_brain.failures import failure
from rockygpt_brain.jev import Jev, TypesafeHttp
from rockygpt_brain.spending import Environment, PostgresLedger
from rockygpt_brain.turn import TurnResult, run_turn
from rockygpt_brain.work import Work, revision

# The API description is for developers; production doesn't publish it.
app = FastAPI(
    title="RockyGPT Brain",
    openapi_url="/openapi.json" if os.getenv("BRAIN_ENVIRONMENT") == "development" else None,
)


@app.get("/health")
@app.head("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


def environment() -> Environment | None:
    match os.getenv("BRAIN_ENVIRONMENT"):
        case "development":
            return "development"
        case "production":
            return "production"
    return None


@cache
def jev_service() -> Jev | None:
    """Jev, paid through this environment's ledger. Built once, so its connections are
    kept between turns."""
    env = environment()
    ledger_url = os.getenv("BRAIN_LEDGER_DATABASE_URL", "").strip()
    key = os.getenv("BRAIN_TYPESAFE_API_KEY", "").strip()
    if env is None or not ledger_url or not key:
        return None
    return Jev(TypesafeHttp(key), PostgresLedger(ledger_url, env))


def wire(model: BaseModel) -> dict[str, Any]:
    """Unset optional fields are left out, as the apps expect."""
    return model.model_dump(mode="json", exclude_none=True)


def sse(name: str, model: BaseModel) -> str:
    return f"event: {name}\ndata: {model.model_dump_json(exclude_none=True)}\n\n"


def log_turn(request_id: str, turn: TurnResult, started: float) -> None:
    """One line per turn, and never the student's words: only ids, codes and times."""
    body = turn.body
    jev = turn.metrics.get("jev", {})
    logging.getLogger("uvicorn.error").info("brain_turn %s", json.dumps({
        "requestId": request_id,
        "httpStatus": turn.status,
        "outcome": body.status if isinstance(body, ChatReply) else body.reason,
        "responseMode": turn.metrics.get("responseMode"),
        "safety": turn.safety is not None,
        "jevSkipped": jev.get("skipped"),
        "jevMs": jev.get("elapsedMs"),
        "jevCostNusd": jev.get("costNusd"),
        "elapsedMs": round((monotonic() - started) * 1000),
    }))


def guarded(context: Context, request_id: str, jev: Jev | None,
            work: Work) -> Iterator[ProgressEvent | TurnResult]:
    """The turn, and if a bug stops it, a failure that still carries the emergency help.
    The log gets the error's type and where it happened, not its message, which could
    hold the student's words."""
    try:
        for step in run_turn(context, request_id, jev, work):
            if isinstance(step, ProgressEvent):
                work.step(step)
            yield step
    except Exception as error:
        logging.getLogger("uvicorn.error").error(
            "brain_turn_error %s\n%s",
            json.dumps({"requestId": request_id, "error": type(error).__name__}),
            "".join(traceback.format_tb(error.__traceback__)))
        yield TurnResult(*failure("internal_error", request_id), None,
                         {"responseMode": "internal_error"})


@app.post("/v1/chat", response_model=None)
def chat(
    request: ChatRequest,
    jev: Annotated[Jev | None, Depends(jev_service)],
    x_rockygpt_environment_token: Annotated[str | None, Header()] = None,
    x_rockygpt_diagnostics: Annotated[str | None, Header()] = None,
    accept: Annotated[str | None, Header()] = None,
) -> JSONResponse | StreamingResponse:
    expected_token = os.getenv("STAGING_SERVICE_TOKEN", "").strip()
    if expected_token and not hmac.compare_digest(
        expected_token, x_rockygpt_environment_token or ""
    ):
        raise HTTPException(status_code=401, detail="Environment access token required")
    request_id = str(uuid4())
    started = monotonic()
    context = read_context(request, datetime.now(CAMPUS_TIMEZONE))
    diagnostics = environment() == "development" and x_rockygpt_diagnostics == "1"
    headers = {"X-Request-Id": request_id}

    work = Work(started)

    def finished(turn: TurnResult) -> TurnResult:
        log_turn(request_id, turn, started)
        if not diagnostics:
            return turn
        return turn._replace(body=turn.body.model_copy(update={
            "metrics": turn.metrics,
            "diagnostics": {"brain": {"revision": revision(), "environment": "development"},
                            "startedAt": context.now.isoformat(), "work": work.report()},
        }))

    steps = guarded(context, request_id, jev, work)
    if accept and "text/event-stream" in accept.lower():

        def events() -> Iterator[str]:
            yield sse("progress", ProgressEvent(stage="connecting"))
            for step in steps:
                if isinstance(step, ProgressEvent):
                    yield sse("progress", step)
                else:
                    turn = finished(step)
                    yield sse("result", ResultEvent(status=turn.status, body=turn.body))

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={**headers, "Cache-Control": "no-cache, no-transform",
                     "X-Accel-Buffering": "no"},
        )
    turn = finished(next(step for step in steps if isinstance(step, TurnResult)))
    return JSONResponse(status_code=turn.status, content=wire(turn.body), headers=headers)
