"""Stateless HTTP boundary for the student assistant."""

import asyncio
import hmac
import json
import logging
import os
from datetime import datetime
from threading import BoundedSemaphore
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from rockygpt_brain.accounting import PaidCallError, PostgresLedger
from rockygpt_brain.config import RELEASE, ConfigurationError, load_deployment
from rockygpt_brain.contracts import ChatRequest
from rockygpt_brain.data import CampusData
from rockygpt_brain.engine import InvalidAnswer, run_turn
from rockygpt_brain.limits import BodyLimitMiddleware
from rockygpt_brain.provider import PaidGateway, open_gateway

load_dotenv()
app = FastAPI(title="RockyGPT Brain", version="1.0.0")
app.add_middleware(BodyLimitMiddleware)
CAMPUS_TIMEZONE = ZoneInfo("America/New_York")
TURN_SLOTS = BoundedSemaphore(RELEASE.active_turns)
HTTP_TURN_SECONDS = RELEASE.http_turn_seconds


@app.get("/health")
@app.head("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readiness", response_model=None)
def readiness() -> dict[str, object] | JSONResponse:
    if not os.getenv("DATABASE_URL"):
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    data = CampusData(os.environ["DATABASE_URL"], datetime.now(CAMPUS_TIMEZONE))
    try:
        deployment = load_deployment()
        PostgresLedger(deployment.ledger_url, deployment.environment).readiness()
        if (
            not RELEASE.price.valid_from
            <= datetime.now(CAMPUS_TIMEZONE).date()
            < (RELEASE.price.valid_until)
        ):
            raise ConfigurationError("Price configuration expired")
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
    try:
        load_deployment()
    except ConfigurationError:
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
    gateway: PaidGateway | None = None
    started = monotonic()
    outcome = "unavailable"
    dataset_version: str | None = None
    operational: dict[str, object] = {}
    try:
        deployment = load_deployment()
        data = CampusData(os.getenv("DATABASE_URL", ""), now)
        with open_gateway(deployment, request_id) as gateway:
            result = run_turn(
                request.messages,
                client=gateway,
                data=data,
                model=RELEASE.model,
                now=now,
                metrics=operational,
            )
        outcome = result["status"]
        dataset_version = result.get("datasetVersion")
        operational = result["metrics"]
        usage = gateway.usage.report()
        # Billing details remain in the operational ledger/log, not student answers.
        result["metrics"].update(
            {key: value for key, value in usage.items() if key not in {"costNusd", "unsettledNusd"}}
        )
        return {**result, "requestId": request_id}
    except ConfigurationError:
        return failure(503, "model_not_configured", request_id)
    except PaidCallError as error:
        outcome = error.code
        status = {
            "budget_exhausted": 429,
            "model_quota_exhausted": 429,
            "rate_limited": 429,
            "model_timeout": 504,
            "model_provider_error": 502,
            "context_limit": 422,
        }.get(error.code, 503)
        resources: list[dict[str, str]] = []
        if error.code == "budget_exhausted" and data is not None:
            try:
                data.deadline = min(data.deadline or started + 3.0, monotonic() + 3.0)
                resources = data.resources()
            except Exception:
                resources = []  # Budget responses also work without campus data.
        return failure(status, error.code, request_id, reset_at=error.reset_at, resources=resources)
    except TimeoutError:
        outcome = "model_timeout"
        return failure(504, "model_timeout", request_id)
    except InvalidAnswer as error:
        outcome = "invalid_model_output"
        # Fixed reason codes only: no student text, raw model output, or provider secrets.
        logging.getLogger(__name__).warning(
            "Brain answer rejected request_id=%s reason=%s", request_id, error.code
        )
        return failure(502, "invalid_model_output", request_id)
    finally:
        try:
            summary = {
                "requestId": request_id,
                "status": outcome,
                "datasetVersion": dataset_version or operational.get("datasetVersion"),
                "toolResults": operational.get("toolResults", []),
                "responseMode": operational.get("responseMode"),
                "elapsedMs": round((monotonic() - started) * 1000),
                "fallbackUsed": outcome in {"unavailable", "budget_exhausted"},
                "retrievalMs": operational.get("retrievalMs", 0),
                **(gateway.usage.report() if gateway is not None else {}),
            }
            logging.getLogger("uvicorn.error").info("brain_turn %s", json.dumps(summary))
            if gateway is not None:
                try:
                    gateway.finish(summary)
                except PaidCallError:
                    logging.getLogger(__name__).warning(
                        "Brain telemetry unavailable request_id=%s", request_id
                    )
            if data is not None:
                data.close()
        finally:
            slots.release()


def failure(
    status: int,
    reason: str,
    request_id: str,
    *,
    reset_at: str | None = None,
    resources: list[dict[str, str]] | None = None,
) -> JSONResponse:
    message = (
        "RockyGPT is currently unavailable. Please use Ramapo's official resources "
        "for campus information."
        if reason in {"model_quota_exhausted", "budget_exhausted"}
        else "Rocky couldn't produce a reliable answer just now. Please try again."
    )
    if reason == "budget_exhausted":
        message = "RockyGPT's monthly AI allowance is exhausted. Use the official campus resources."
    elif reason == "context_limit":
        message = "This conversation exceeds the supported context limit. Start a shorter chat."
    elif reason not in {
        "busy",
        "rate_limited",
        "model_timeout",
        "model_unreachable",
        "model_provider_error",
        "invalid_model_output",
        "model_quota_exhausted",
    }:
        message = "RockyGPT is unavailable until its service configuration is restored."
    details: dict[str, object] = {}
    if reset_at is not None:
        details["resetAt"] = reset_at
    if resources:
        details["resources"] = resources
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": reason,
                "message": message,
                "retryable": reason
                in {
                    "busy",
                    "rate_limited",
                    "model_timeout",
                    "model_unreachable",
                    "model_provider_error",
                    "invalid_model_output",
                },
                **details,
            },
            "reason": reason,
            "requestId": request_id,
        },
    )
