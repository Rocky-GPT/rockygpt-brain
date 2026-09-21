"""Stateless HTTP boundary for the student assistant."""

import asyncio
import hmac
import json
import logging
import os
from datetime import datetime
from importlib.resources import files
from threading import BoundedSemaphore, Event
from time import monotonic
from typing import Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

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


@app.get("/v1/logs")
def get_logs(limit: int = 50) -> dict[str, Any]:
    ledger_url = os.getenv("BRAIN_LEDGER_DATABASE_URL")
    env = os.getenv("BRAIN_ENVIRONMENT", "development")
    if ledger_url:
        try:
            import certifi
            import psycopg
            from psycopg import sql
            from psycopg.rows import dict_row

            conn_opts: dict[str, Any] = {"autocommit": True, "connect_timeout": 2}
            if "sslrootcert" not in ledger_url and not os.getenv("PGSSLROOTCERT"):
                conn_opts["sslrootcert"] = certifi.where()

            with psycopg.connect(ledger_url, **conn_opts) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(f"brain_{env}")))
                    cur.execute(
                        "SELECT request_id, created_at, summary FROM brain_ops.turns "
                        "WHERE summary->>'question' IS NOT NULL "
                        "ORDER BY created_at DESC LIMIT %s",
                        (limit,),
                    )
                    rows = cur.fetchall()
                    cur.execute(
                        "SELECT count(*) as total FROM brain_ops.turns "
                        "WHERE summary->>'question' IS NOT NULL"
                    )
                    total_row = cur.fetchone()
                    total = total_row["total"] if total_row else len(rows)

            entries = []
            for r in rows:
                s = r["summary"] if isinstance(r["summary"], dict) else json.loads(r["summary"])
                entries.append({
                    "timestamp": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                    "requestId": str(r["request_id"]),
                    "question": s.get("question", ""),
                    "messages": s.get("messages", []),
                    "answer": s.get("answer"),
                    "status": s.get("status", "answered"),
                    "elapsedMs": s.get("elapsedMs", 0),
                    "citations": s.get("citations", []),
                })
            return {"logs": entries, "total": total}
        except Exception as err:
            logging.getLogger(__name__).warning("Database turns query failed: %s", err)
            return {"logs": [], "total": 0, "error": str(err)}

    return {"logs": [], "total": 0}


class FeedbackPayload(BaseModel):
    requestId: str
    rating: int
    category: str | None = None
    comments: str | None = None
    question: str | None = None
    answer: str | None = None


@app.post("/v1/feedback")
def submit_feedback(payload: FeedbackPayload) -> dict[str, Any]:
    ledger_url = os.getenv("BRAIN_LEDGER_DATABASE_URL")
    env = os.getenv("BRAIN_ENVIRONMENT", "development")
    if not ledger_url:
        return {"success": False, "error": "Database ledger URL not configured"}
    try:
        import certifi
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        conn_opts: dict[str, Any] = {"autocommit": True, "connect_timeout": 5}
        if "sslrootcert" not in ledger_url and not os.getenv("PGSSLROOTCERT"):
            conn_opts["sslrootcert"] = certifi.where()

        rating = 1 if payload.rating > 0 else -1
        question = payload.question or ""
        answer = payload.answer or ""

        with psycopg.connect(ledger_url, **conn_opts) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if not question or not answer:
                    try:
                        cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(f"brain_{env}")))
                        cur.execute(
                            "SELECT summary FROM brain_ops.turns WHERE request_id = %s",
                            (payload.requestId,),
                        )
                        row = cur.fetchone()
                        if row and row.get("summary"):
                            s = row["summary"] if isinstance(row["summary"], dict) else json.loads(row["summary"])
                            question = question or s.get("question", "")
                            answer = answer or s.get("answer", "")
                    except Exception:
                        pass

                cur.execute(sql.SQL("RESET ROLE"))
                cur.execute(
                    """
                    INSERT INTO rockygpt_v2.feedback (request_id, question, answer, rating, category, comments)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (request_id) DO UPDATE SET
                        rating = EXCLUDED.rating,
                        category = EXCLUDED.category,
                        comments = EXCLUDED.comments,
                        question = CASE WHEN EXCLUDED.question <> '' THEN EXCLUDED.question ELSE rockygpt_v2.feedback.question END,
                        answer = CASE WHEN EXCLUDED.answer <> '' THEN EXCLUDED.answer ELSE rockygpt_v2.feedback.answer END
                    """,
                    (payload.requestId, question or "N/A", answer or "N/A", rating, payload.category, payload.comments),
                )
        return {"success": True}
    except Exception as err:
        logging.getLogger(__name__).warning("Failed to submit feedback: %s", err)
        return {"success": False, "error": str(err)}


@app.get("/v1/feedback")
def get_feedback(limit: int = 50) -> dict[str, Any]:
    ledger_url = os.getenv("BRAIN_LEDGER_DATABASE_URL")
    if not ledger_url:
        return {"feedback": [], "total": 0}
    try:
        import certifi
        import psycopg
        from psycopg.rows import dict_row

        conn_opts: dict[str, Any] = {"autocommit": True, "connect_timeout": 5}
        if "sslrootcert" not in ledger_url and not os.getenv("PGSSLROOTCERT"):
            conn_opts["sslrootcert"] = certifi.where()

        with psycopg.connect(ledger_url, **conn_opts) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id, request_id, question, answer, rating, category, comments, created_at
                    FROM rockygpt_v2.feedback
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
                cur.execute("SELECT count(*) as total FROM rockygpt_v2.feedback")
                total_row = cur.fetchone()
                total = total_row["total"] if total_row else len(rows)

        entries = []
        for r in rows:
            entries.append({
                "id": str(r["id"]),
                "requestId": str(r["request_id"]),
                "question": r["question"],
                "answer": r["answer"],
                "rating": r["rating"],
                "category": r["category"],
                "comments": r["comments"],
                "createdAt": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
            })
        return {"feedback": entries, "total": total}
    except Exception as err:
        logging.getLogger(__name__).warning("Failed to fetch feedback: %s", err)
        return {"feedback": [], "total": 0, "error": str(err)}


class EvalRunPayload(BaseModel):
    runId: str
    suite: str
    totalTests: int
    passed: int
    failed: int
    durationMs: int
    summary: dict[str, Any] = Field(default_factory=dict)


@app.post("/v1/evals/runs")
def record_eval_run(payload: EvalRunPayload) -> dict[str, Any]:
    ledger_url = os.getenv("BRAIN_LEDGER_DATABASE_URL")
    if not ledger_url:
        return {"success": False, "error": "Database ledger URL not configured"}
    try:
        import certifi
        import psycopg
        from psycopg.types.json import Jsonb

        conn_opts: dict[str, Any] = {"autocommit": True, "connect_timeout": 5}
        if "sslrootcert" not in ledger_url and not os.getenv("PGSSLROOTCERT"):
            conn_opts["sslrootcert"] = certifi.where()

        with psycopg.connect(ledger_url, **conn_opts) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO brain_ops.eval_runs (run_id, suite, total_tests, passed, failed, duration_ms, summary)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        total_tests = EXCLUDED.total_tests,
                        passed = EXCLUDED.passed,
                        failed = EXCLUDED.failed,
                        duration_ms = EXCLUDED.duration_ms,
                        summary = EXCLUDED.summary
                    """,
                    (
                        payload.runId,
                        payload.suite,
                        payload.totalTests,
                        payload.passed,
                        payload.failed,
                        payload.durationMs,
                        Jsonb(payload.summary),
                    ),
                )
        return {"success": True}
    except Exception as err:
        logging.getLogger(__name__).warning("Failed to record eval run: %s", err)
        return {"success": False, "error": str(err)}


@app.get("/v1/evals/runs")
def get_eval_runs(limit: int = 50) -> dict[str, Any]:
    ledger_url = os.getenv("BRAIN_LEDGER_DATABASE_URL")
    if not ledger_url:
        return {"runs": [], "total": 0}
    try:
        import certifi
        import psycopg
        from psycopg.rows import dict_row

        conn_opts: dict[str, Any] = {"autocommit": True, "connect_timeout": 5}
        if "sslrootcert" not in ledger_url and not os.getenv("PGSSLROOTCERT"):
            conn_opts["sslrootcert"] = certifi.where()

        with psycopg.connect(ledger_url, **conn_opts) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT id, run_id, suite, total_tests, passed, failed, duration_ms, summary, created_at
                    FROM brain_ops.eval_runs
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
                cur.execute("SELECT count(*) as total FROM brain_ops.eval_runs")
                total_row = cur.fetchone()
                total = total_row["total"] if total_row else len(rows)

        entries = []
        for r in rows:
            entries.append({
                "id": str(r["id"]),
                "runId": r["run_id"],
                "suite": r["suite"],
                "totalTests": r["total_tests"],
                "passed": r["passed"],
                "failed": r["failed"],
                "durationMs": r["duration_ms"],
                "summary": r["summary"] if isinstance(r["summary"], dict) else json.loads(r["summary"]),
                "createdAt": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
            })
        return {"runs": entries, "total": total}
    except Exception as err:
        logging.getLogger(__name__).warning("Failed to fetch eval runs: %s", err)
        return {"runs": [], "total": 0, "error": str(err)}


@app.get("/v1/prompts")
def get_prompts() -> dict[str, Any]:
    prompt_md = files("rockygpt_brain").joinpath("prompt.md").read_text(encoding="utf-8")
    review_md = files("rockygpt_brain").joinpath("review.md").read_text(encoding="utf-8")
    return {
        "model": RELEASE.model,
        "prompt": prompt_md,
        "review": review_md,
        "draftReasoning": RELEASE.draft_reasoning,
        "reviewReasoning": RELEASE.review_reasoning,
    }


@app.get("/v1/config")
def get_config() -> dict[str, Any]:
    from rockygpt_brain.config import MONTHLY_CAP_NUSD
    return {
        **RELEASE.model_dump(mode="json"),
        "monthlyCapNusd": MONTHLY_CAP_NUSD,
        "environment": os.getenv("BRAIN_ENVIRONMENT", "development"),
        "timezone": "America/New_York",
    }


@app.get("/v1/releases")
def get_releases() -> Any:
    release_json = files("rockygpt_brain").joinpath("release.json").read_text(encoding="utf-8")
    config_release = json.loads(release_json)
    database_url = os.getenv("DATABASE_URL", "")
    dataset_info = None
    if database_url:
        try:
            data = CampusData(database_url, datetime.now(ZoneInfo("America/New_York")))
            data._ensure_loaded()
            activated = data.dataset.get("activated_at")
            dataset_info = {
                "id": data.dataset.get("id"),
                "version": data.dataset.get("version"),
                "activatedAt": activated.isoformat() if hasattr(activated, "isoformat") else str(activated),
                "sourcesCount": len(data.sources),
                "sources": [
                    {
                        "id": s.get("id"),
                        "source_key": s.get("source_key"),
                        "title": s.get("title"),
                        "canonical_url": s.get("canonical_url"),
                        "trust_tier": s.get("trust_tier"),
                        "provenance_status": s.get("provenance_status"),
                    }
                    for s in list(data.sources.values())[:50]
                ],
            }
            data.close()
        except Exception as e:
            dataset_info = {"error": str(e)}
    return {
        "brainRelease": config_release,
        "dataset": dataset_info,
    }


CAPABILITIES_CATALOG = [
    {
        "capability": "critical_facts",
        "describes": "Concise verified campus facts, emergency contacts, action links, and key dates.",
        "filters": [
            {"field": "name", "type": "string", "description": "Fact key or topic"},
        ],
        "fields": ["fact_key", "fact_value", "verified_at"],
    },
    {
        "capability": "contacts",
        "describes": "Campus directories, staff, offices, phone numbers, and email addresses.",
        "filters": [
            {"field": "name", "type": "string", "description": "Person or office name"},
            {"field": "department", "type": "string", "description": "Campus department"},
        ],
        "fields": [
            "type",
            "name",
            "title",
            "department",
            "phones",
            "email",
            "offices",
            "status",
            "preferred_contact",
        ],
    },
    {
        "capability": "campus_hours",
        "describes": "Operational opening and closing hours for campus buildings and administrative offices.",
        "filters": [
            {"field": "venue", "type": "string", "description": "Building or facility"},
            {"field": "date", "type": "iso-date", "description": "Date of interest"},
        ],
        "fields": ["venue", "day_of_week", "open_time", "close_time", "notes"],
    },
    {
        "capability": "dining_hours",
        "describes": "Operating hours and meal periods for campus dining facilities.",
        "filters": [
            {"field": "venue", "type": "string", "description": "Dining location"},
        ],
        "fields": ["venue", "meal_period", "open_time", "close_time"],
    },
    {
        "capability": "menu",
        "describes": "Daily campus dining menu offerings, ingredients, allergens, and nutritional info.",
        "filters": [
            {"field": "date", "type": "iso-date", "description": "Menu date"},
            {"field": "venue", "type": "string", "description": "Dining location"},
        ],
        "fields": ["venue", "date", "station", "item_name", "calories", "allergens"],
    },
    {
        "capability": "events",
        "describes": "Campus events, activities, student programming, and workshops from Archway.",
        "filters": [
            {"field": "date_from", "type": "iso-date", "description": "Start date"},
            {"field": "category", "type": "string", "description": "Event category"},
        ],
        "fields": ["title", "starts_at", "ends_at", "location", "organization"],
    },
    {
        "capability": "shuttle",
        "describes": "Roadrunner Express shuttle routes, stops, schedules, and transit loops.",
        "filters": [
            {"field": "route", "type": "string", "description": "Shuttle route name"},
        ],
        "fields": ["route", "stop_name", "departure_time", "direction"],
    },
    {
        "capability": "calendar",
        "describes": "Official Ramapo academic calendar milestones, deadlines, and semester dates.",
        "filters": [
            {"field": "term", "type": "string", "description": "Semester term"},
        ],
        "fields": ["event", "date", "term"],
    },
    {
        "capability": "clubs",
        "describes": "Student clubs, greek life, and cultural organizations recognized by SGA.",
        "filters": [
            {"field": "category", "type": "string", "description": "Club category"},
        ],
        "fields": ["name", "category", "email", "description"],
    },
    {
        "capability": "programs",
        "describes": "Academic degree programs, majors, minors, concentrations, and schools.",
        "filters": [
            {"field": "name", "type": "string", "description": "Program name or major"},
        ],
        "fields": ["name", "degree", "program_kind", "school", "description", "program_url"],
    },
    {
        "capability": "program_requirements",
        "describes": "Degree requirements, graduation rules, and required course sequences for academic programs.",
        "filters": [
            {"field": "program", "type": "string", "description": "Degree program name"},
        ],
        "fields": ["program", "section", "rule"],
    },
    {
        "capability": "courses",
        "describes": "Course catalog offerings, prerequisites, credit hours, and subject descriptions.",
        "filters": [
            {"field": "subject", "type": "string", "description": "Academic discipline"},
        ],
        "fields": ["course_code", "title", "credits", "prerequisites", "description"],
    },
    {
        "capability": "faculty",
        "describes": "Faculty directory profiles, schools, teaching fields, and research interests.",
        "filters": [
            {"field": "name", "type": "string", "description": "Professor or instructor name"},
            {"field": "school", "type": "string", "description": "Academic school"},
        ],
        "fields": ["name", "title", "school", "email", "phone", "office"],
    },
    {
        "capability": "documents",
        "describes": "Official campus policies, student handbook regulations, and college bylaws.",
        "filters": [
            {"field": "query", "type": "string", "description": "Keyword search query"},
        ],
        "fields": ["title", "url", "category", "snippet"],
    },
]


@app.get("/v1/capabilities")
def get_capabilities() -> dict[str, Any]:
    return {"capabilities": CAPABILITIES_CATALOG}


@app.get("/v1/capabilities/{name}/records", response_model=None)
def get_capability_records(name: str, limit: int = 5000) -> dict[str, Any] | JSONResponse:
    from rockygpt_brain.retrieval.models import COLLECTIONS, SearchQuery

    if name not in COLLECTIONS:
        return JSONResponse(status_code=404, content={"error": f"Unknown collection: {name}"})
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        return JSONResponse(
            status_code=503, content={"error": "Campus database is not configured."}
        )
    data = None
    try:
        data = CampusData(database_url, datetime.now(ZoneInfo("America/New_York")))
        data._ensure_loaded()
        if name == "documents":
            records, _ = data._documents(SearchQuery(collection="documents", query="", limit=min(limit, 100)))
        else:
            records = data._load(name)
            if limit:
                records = records[:limit]

        formatted: list[dict[str, Any]] = []
        for r in records:
            if name == "contacts":
                f = r.get("fields", {})
                item: dict[str, Any] = {"id": r.get("id")}
                for field in (
                    "type", "name", "title", "department", "phones", "email",
                    "offices", "status", "preferred_contact",
                ):
                    if f.get(field):
                        item[field] = f[field]
                item.setdefault("name", r.get("title", ""))
                formatted.append(item)
            else:
                item = {"id": r.get("id"), "title": r.get("title", ""), **r.get("fields", {})}
                if r.get("valid_from"):
                    item["date"] = r.get("valid_from")
                if r.get("valid_until") and r.get("valid_until") != r.get("valid_from"):
                    item["valid_until"] = r.get("valid_until")
                if name == "documents":
                    item["url"] = r.get("url", "")
                    item["snippet"] = r.get("content", "")
                formatted.append(item)
        return {"returned": len(formatted), "records": formatted}
    except Exception:
        logging.getLogger(__name__).exception("Capability records lookup failed: %s", name)
        return JSONResponse(
            status_code=503,
            content={"error": "Campus records could not be loaded. Check the Brain logs for details."},
        )
    finally:
        if data is not None:
            data.close()


@app.get("/v1/documents")
def get_documents() -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        return {"documents": [], "total": 0}
    data = None
    try:
        data = CampusData(database_url, datetime.now(ZoneInfo("America/New_York")))
        data._ensure_loaded()
        rows = data._fetch(
            """
            SELECT d.id::text, d.title, length(d.content) as content_length, 
                   d.collected_at, d.metadata,
                   s.canonical_url, s.source_key, s.trust_tier,
                   count(c.id) as chunk_count
            FROM rockygpt_v2.documents d
            JOIN rockygpt_v2.sources s ON s.id = d.source_id
            LEFT JOIN rockygpt_v2.document_chunks c ON c.document_id = d.id
            WHERE d.dataset_version_id = %s::uuid
            GROUP BY d.id, d.title, d.content, d.collected_at, d.metadata, s.canonical_url, s.source_key, s.trust_tier
            ORDER BY d.title
            """,
            (data.dataset["id"],),
        )
        documents = []
        for r in rows:
            collected = r.get("collected_at")
            documents.append({
                "id": r["id"],
                "title": r["title"],
                "contentLength": r["content_length"],
                "chunkCount": r["chunk_count"],
                "canonicalUrl": r["canonical_url"],
                "sourceKey": r["source_key"],
                "trustTier": r["trust_tier"],
                "collectedAt": collected.isoformat() if hasattr(collected, "isoformat") else str(collected),
                "metadata": r.get("metadata") or {},
            })
        return {"documents": documents, "total": len(documents)}
    except Exception as e:
        return {"documents": [], "total": 0, "error": str(e)}
    finally:
        if data is not None:
            data.close()


@app.get("/v1/documents/{document_id}")
def get_document(document_id: str) -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        return {"error": "Database not configured"}
    data = None
    try:
        data = CampusData(database_url, datetime.now(ZoneInfo("America/New_York")))
        data._ensure_loaded()
        doc_rows = data._fetch(
            """
            SELECT d.id::text, d.title, d.content, d.collected_at, d.metadata,
                   s.canonical_url, s.source_key, s.trust_tier
            FROM rockygpt_v2.documents d
            JOIN rockygpt_v2.sources s ON s.id = d.source_id
            WHERE d.id = %s::uuid AND d.dataset_version_id = %s::uuid
            LIMIT 1
            """,
            (document_id, data.dataset["id"]),
        )
        if not doc_rows:
            return {"error": f"Document not found: {document_id}"}

        doc = doc_rows[0]
        chunks_rows = data._fetch(
            """
            SELECT c.id::text, c.chunk_index, c.content, c.metadata
            FROM rockygpt_v2.document_chunks c
            WHERE c.document_id = %s::uuid
            ORDER BY c.chunk_index ASC
            """,
            (document_id,),
        )
        collected = doc.get("collected_at")
        chunks = [
            {
                "id": c["id"],
                "chunkIndex": c["chunk_index"],
                "content": c["content"],
                "headingPath": (c.get("metadata") or {}).get("headingPath", ""),
                "metadata": c.get("metadata") or {},
            }
            for c in chunks_rows
        ]
        return {
            "id": doc["id"],
            "title": doc["title"],
            "content": doc["content"],
            "contentLength": len(doc["content"]) if doc.get("content") else 0,
            "canonicalUrl": doc["canonical_url"],
            "sourceKey": doc["source_key"],
            "trustTier": doc["trust_tier"],
            "collectedAt": collected.isoformat() if hasattr(collected, "isoformat") else str(collected),
            "metadata": doc.get("metadata") or {},
            "chunkCount": len(chunks),
            "chunks": chunks,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        if data is not None:
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
                "citations": citations,
                **(gateway.usage.report() if gateway is not None else {}),
            }

            # 1. Log to console / uvicorn logger
            logging.getLogger("uvicorn.error").info("brain_turn %s", json.dumps(summary))

            # 2. Save to PostgreSQL ledger (brain_ops.turns table)
            if gateway is not None:
                try:
                    gateway.finish(summary)
                except PaidCallError:
                    logging.getLogger(__name__).warning(
                        "Brain telemetry unavailable request_id=%s", request_id
                    )
            elif deployment is not None:
                try:
                    from rockygpt_brain.governance.accounting import PostgresLedger
                    ledger = PostgresLedger(deployment.ledger_url, deployment.environment)
                    ledger.record_turn(request_id, summary)
                except Exception as db_err:
                    logging.getLogger(__name__).warning(
                        "Brain turn fallback record failed request_id=%s: %s", request_id, db_err
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
