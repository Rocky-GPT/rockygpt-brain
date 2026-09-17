"""Stateless HTTP boundary for the student assistant."""

import asyncio
import hmac
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from threading import BoundedSemaphore, Event
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from rockygpt_brain.api.stream import stream_turn
from rockygpt_brain.campus.progress import ProgressCallback, ProgressUpdate, TurnCancelled
from rockygpt_brain.config import RELEASE, ConfigurationError, load_deployment
from rockygpt_brain.contracts import ChatRequest
from rockygpt_brain.core import InvalidAnswer, PaidGateway, open_gateway, run_turn
from rockygpt_brain.governance import BodyLimitMiddleware, PaidCallError, PostgresLedger
from rockygpt_brain.retrieval import CampusData

load_dotenv()
app = FastAPI(title="RockyGPT Brain", version="1.0.0")
app.add_middleware(BodyLimitMiddleware)
CAMPUS_TIMEZONE = ZoneInfo("America/New_York")
TURN_SLOTS = BoundedSemaphore(RELEASE.active_turns)
HTTP_TURN_SECONDS = RELEASE.http_turn_seconds
WORKERS: set[asyncio.Task[dict[str, object] | JSONResponse]] = set()


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
    accept: str | None = Header(default=None),
) -> dict[str, object] | JSONResponse | StreamingResponse:
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
    updates: asyncio.Queue[ProgressUpdate] = asyncio.Queue(maxsize=32)
    stopped = Event()
    loop = asyncio.get_running_loop()

    def enqueue(stage: ProgressUpdate) -> None:
        if stopped.is_set():
            return
        if updates.full():
            updates.get_nowait()
        updates.put_nowait(stage)

    def progress(stage: ProgressUpdate) -> None:
        if stopped.is_set():
            raise TurnCancelled()
        loop.call_soon_threadsafe(enqueue, stage)

    streaming = bool(accept and "text/event-stream" in accept.lower())
    worker = asyncio.create_task(
        asyncio.to_thread(
            chat_worker, request, request_id, now, slots, progress if streaming else None
        )
    )
    WORKERS.add(worker)

    def finished(task: asyncio.Task[dict[str, object] | JSONResponse]) -> None:
        WORKERS.discard(task)
        if not task.cancelled():
            task.exception()  # Observe exceptions even after an HTTP disconnect.

    worker.add_done_callback(finished)
    if streaming:
        return StreamingResponse(
            stream_turn(
                worker,
                updates,
                stopped,
                HTTP_TURN_SECONDS,
                lambda status, reason: failure(status, reason, request_id),
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "X-Request-Id": request_id,
            },
        )
    try:
        # A timed-out worker retains its slot until its bounded I/O and cleanup
        # finish. Shielding also prevents cancelling a worker queued to start.
        return await asyncio.wait_for(asyncio.shield(worker), timeout=HTTP_TURN_SECONDS)
    except TimeoutError:
        return failure(504, "model_timeout", request_id)


def log_student_question(entry: dict[str, object]) -> None:
    """Append unfiltered student interaction to real-time JSONL audit log."""
    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "student_questions.jsonl"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as err:
        logging.getLogger(__name__).warning("Failed to append student question log: %s", err)


def chat_worker(
    request: ChatRequest,
    request_id: str,
    now: datetime,
    slots: BoundedSemaphore,
    progress: ProgressCallback | None = None,
) -> dict[str, object] | JSONResponse:
    data: CampusData | None = None
    gateway: PaidGateway | None = None
    started = monotonic()
    outcome = "unavailable"
    dataset_version: str | None = None
    operational: dict[str, object] = {}
    result: dict[str, object] | JSONResponse | None = None
    question_text = request.messages[-1].content if request.messages else ""
    raw_messages = [m.model_dump() for m in request.messages]
    try:
        deployment = load_deployment()
        data = CampusData(os.getenv("DATABASE_URL", ""), now)
        with open_gateway(deployment, request_id) as gateway:
            turn_result = run_turn(
                request.messages,
                client=gateway,
                data=data,
                model=RELEASE.model,
                now=now,
                metrics=operational,
                progress=progress,
            )
            result = turn_result
        outcome = result["status"]
        dataset_version = result.get("datasetVersion")
        operational = result["metrics"]
        usage = gateway.usage.report()
        # Billing details remain in the operational ledger/log, not student answers.
        result["metrics"].update(
            {key: value for key, value in usage.items() if key not in {"costNusd", "unsettledNusd"}}
        )
        return {**result, "requestId": request_id}
    except TurnCancelled:
        outcome = "request_cancelled"
        return failure(499, "request_cancelled", request_id)
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
            "retrieval_context_limit": 422,
            "turn_cost_limit": 422,
            "model_call_limit": 422,
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
            answer_text = None
            citations: Any = []
            if isinstance(result, dict):
                answer_text = result.get("answer")
                citations = result.get("citations", [])

            summary = {
                "requestId": request_id,
                "question": question_text,
                "messages": raw_messages,
                "answer": answer_text,
                "status": outcome,
                "datasetVersion": dataset_version or operational.get("datasetVersion"),
                "toolResults": operational.get("toolResults", []),
                "responseMode": operational.get("responseMode"),
                "elapsedMs": round((monotonic() - started) * 1000),
                "fallbackUsed": operational.get("fallbackUsed", False)
                or outcome in {"unavailable", "budget_exhausted"},
                "fallbackReason": operational.get("fallbackReason"),
                "validationFailures": operational.get("validationFailures", []),
                "retrievalMs": operational.get("retrievalMs", 0),
                **(gateway.usage.report() if gateway is not None else {}),
            }

            # 1. Append raw interaction to logs/student_questions.jsonl
            log_student_question({
                "timestamp": now.isoformat(),
                "requestId": request_id,
                "question": question_text,
                "messages": raw_messages,
                "answer": answer_text,
                "status": outcome,
                "elapsedMs": summary["elapsedMs"],
                "citations": citations,
            })

            # 2. Log to console / uvicorn logger
            logging.getLogger("uvicorn.error").info("brain_turn %s", json.dumps(summary))

            # 3. Save to PostgreSQL ledger (brain_ops.turns table)
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
    elif reason == "retrieval_context_limit":
        message = (
            "The information needed for this answer exceeds RockyGPT's processing limit. "
            "Try narrowing the request to one topic, place, or date."
        )
    elif reason in {"turn_cost_limit", "model_call_limit"}:
        message = (
            "This request exceeds RockyGPT's per-answer processing allowance. "
            "Please ask a more focused question."
        )
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
