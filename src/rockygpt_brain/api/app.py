"""Stateless HTTP boundary for the student assistant."""

import asyncio
import hmac
import logging
import os
from datetime import datetime
from threading import BoundedSemaphore
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from rockygpt_brain.contracts import ChatRequest
from rockygpt_brain.data import CampusData
from rockygpt_brain.engine import InvalidAnswer, run_turn
from rockygpt_brain.limits import BodyLimitMiddleware

load_dotenv()
app = FastAPI(title="RockyGPT Brain", version="1.0.0")
app.add_middleware(BodyLimitMiddleware)
CAMPUS_TIMEZONE = ZoneInfo("America/New_York")
TURN_SLOTS = BoundedSemaphore(4)
HTTP_TURN_SECONDS = 52.0


@app.get("/health")
@app.head("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readiness", response_model=None)
def readiness() -> dict[str, object] | JSONResponse:
    if not os.getenv("OPENAI_API_KEY") or not os.getenv("DATABASE_URL"):
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    data = CampusData(os.environ["DATABASE_URL"], datetime.now(CAMPUS_TIMEZONE))
    try:
        return {"status": "ready", "campus_data": data.readiness()}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    finally:
        data.close()


@app.post("/v1/chat", response_model=None)
async def chat(
    request: ChatRequest,
    x_rockygpt_environment_token: str | None = Header(default=None),
) -> dict[str, object] | JSONResponse:
    expected_token = os.getenv("STAGING_SERVICE_TOKEN", "").strip()
    if expected_token and not hmac.compare_digest(
        expected_token, x_rockygpt_environment_token or ""
    ):
        raise HTTPException(status_code=401, detail="Environment access token required")
    request_id = str(uuid4())
    if not os.getenv("OPENAI_API_KEY"):
        return failure(503, "model_not_configured", request_id)
    slots = TURN_SLOTS
    if not slots.acquire(blocking=False):
        return failure(429, "busy", request_id)
    now = datetime.now(CAMPUS_TIMEZONE)
    worker = asyncio.create_task(asyncio.to_thread(chat_worker, request, request_id, now, slots))
    try:
        # A timed-out worker retains its slot until its bounded I/O and cleanup
        # finish. Shielding also prevents cancelling a worker queued to start.
        return await asyncio.wait_for(asyncio.shield(worker), timeout=HTTP_TURN_SECONDS)
    except TimeoutError:
        return failure(504, "model_timeout", request_id)


def chat_worker(
    request: ChatRequest,
    request_id: str,
    now: datetime,
    slots: BoundedSemaphore,
) -> dict[str, object] | JSONResponse:
    data: CampusData | None = None
    try:
        data = CampusData(os.getenv("DATABASE_URL", ""), now)
        with OpenAI(max_retries=0, timeout=30.0) as client:
            result = run_turn(
                request.messages,
                client=client,
                data=data,
                model=os.getenv("OPENAI_CHAT_MODEL") or "gpt-5.4",
                now=now,
            )
        return {**result, "requestId": request_id}
    except RateLimitError as error:
        if error.type == "insufficient_quota" or error.code in {
            "insufficient_quota",
            "credit_balance_exhausted",
        }:
            return failure(429, "model_quota_exhausted", request_id)
        return failure(429, "rate_limited", request_id)
    except (APITimeoutError, TimeoutError):
        return failure(504, "model_timeout", request_id)
    except APIConnectionError:
        return failure(503, "model_unreachable", request_id)
    except APIStatusError:
        return failure(502, "model_provider_error", request_id)
    except InvalidAnswer as error:
        # Fixed reason codes only: no student text, raw model output, or provider secrets.
        logging.getLogger(__name__).warning(
            "Brain answer rejected request_id=%s reason=%s", request_id, error.code
        )
        return failure(502, "invalid_model_output", request_id)
    finally:
        try:
            if data is not None:
                data.close()
        finally:
            slots.release()


def failure(status: int, reason: str, request_id: str) -> JSONResponse:
    message = (
        "RockyGPT is currently unavailable. Please use Ramapo's official resources "
        "for campus information."
        if reason == "model_quota_exhausted"
        else "Rocky couldn't produce a reliable answer just now. Please try again."
    )
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": reason,
                "message": message,
                "retryable": reason not in {"model_not_configured", "model_quota_exhausted"},
            },
            "reason": reason,
            "requestId": request_id,
        },
    )
