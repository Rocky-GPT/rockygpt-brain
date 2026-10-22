"""Capture a prospective synthetic suite through the real development /v1/chat.

This runner does not grade semantics, retry failures, or load environment files.
It requires explicit live execution and an explicit conservative spending ceiling.
Use the normal local environment launcher to supply credentials before invocation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, DecimalException
from pathlib import Path
from time import perf_counter
from typing import Any, TextIO

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from rockygpt_brain.api.app import create_app
from rockygpt_brain.engine import SYSTEM_PROMPT, TOOLS, ChatEngine
from rockygpt_brain.provider import Completion, Gateway, TurnBudget
from rockygpt_brain.retrieval import DatasetChanged, PostgresEntityFacts
from rockygpt_brain.retrieval.entity_facts import Snapshot
from rockygpt_brain.settings import ConfigurationError, ProviderSettings

ROOT = Path(__file__).resolve().parents[1]
RUNNER_VERSION = "live-capture-v1"
NUSD_PER_USD = 1_000_000_000


class CaptureError(ValueError):
    """A safe runner configuration error with no secret/provider text."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def usd_to_nusd(value: str) -> int:
    try:
        amount = Decimal(value) * NUSD_PER_USD
        if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (DecimalException, ValueError, OverflowError) as error:
        raise CaptureError("The spending ceiling must be positive with at most nine decimals.") \
            from error


def validate_suite(suite: dict[str, Any]) -> int:
    metadata = suite.get("method_metadata", {})
    if (suite.get("schema_version") != 1 or not isinstance(metadata, dict)
            or metadata.get("synthetic") is not True
            or metadata.get("real_student_traffic") is not False):
        raise CaptureError("Only an explicitly synthetic, non-student-traffic suite is supported.")
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CaptureError("The suite must contain cases.")
    ids: set[str] = set()
    count = 0
    for case in cases:
        if not isinstance(case, dict):
            raise CaptureError("Invalid case.")
        case_id, turns = case.get("id"), case.get("turns")
        omitted = case.get("omittedMessages", 0)
        if (not isinstance(case_id, str) or not case_id or case_id in ids
                or not isinstance(turns, list) or not turns
                or type(omitted) is not int or omitted < 0):
            raise CaptureError("Cases require unique IDs, turns, and valid omittedMessages.")
        ids.add(case_id)
        for turn in turns:
            if (not isinstance(turn, dict) or not isinstance(turn.get("message"), str)
                    or not turn["message"].strip()):
                raise CaptureError("Each turn must contain a nonempty student message.")
            count += 1
    protocol = metadata.get("execution_protocol", {})
    if not isinstance(protocol, dict):
        raise CaptureError("The execution protocol must be an object.")
    declared = protocol.get("student_turn_count")
    if declared is not None and (type(declared) is not int or declared != count):
        raise CaptureError("The declared student-turn count does not match the cases.")
    return count


def preflight(
    suite: dict[str, Any], oracle: dict[str, Any], settings: ProviderSettings,
    max_total_nusd: int,
) -> int:
    if settings.environment != "development":
        raise CaptureError("Live capture is restricted to the development environment.")
    count = validate_suite(suite)
    for key in ("dataset_version", "identity_hash"):
        if not isinstance(oracle.get(key), str) or not oracle[key].strip():
            raise CaptureError("The oracle must identify a dataset version and identity hash.")
    captured = oracle.get("captured_at")
    if captured is not None:
        try:
            captured_at = datetime.fromisoformat(captured)
            if captured_at.tzinfo is None or captured_at > datetime.now(UTC) + timedelta(minutes=5):
                raise ValueError
        except (ValueError, TypeError) as error:
            raise CaptureError("Oracle captured_at must be timezone-aware and not in the future.") \
                from error
    if count * settings.max_turn_nusd > max_total_nusd:
        raise CaptureError("All planned turns at their maximum allowance exceed the run ceiling.")
    return count


class PinnedFacts(PostgresEntityFacts):
    """Apply the oracle pin to preflight AND every actual runtime evidence read."""

    def __init__(self, database_url: str, oracle: dict[str, Any]) -> None:
        super().__init__(database_url)
        self._oracle_version = oracle["dataset_version"]
        self._oracle_identity = oracle["identity_hash"]

    @contextmanager
    def snapshot(self) -> Iterator[Snapshot]:
        with super().snapshot() as snapshot:
            if (snapshot.dataset_version != self._oracle_version
                    or snapshot.identity_hash != self._oracle_identity):
                raise DatasetChanged("The active publication differs from the frozen oracle.")
            yield snapshot

    def assert_publication(self) -> None:
        with self.snapshot():
            pass


class CaptureGateway(Gateway):
    """Record request IDs even when HTTP delivery fails after a paid operation."""

    def __init__(self, settings: ProviderSettings) -> None:
        super().__init__(settings)
        self.request_ids: set[str] = set()

    async def complete(
        self, *, input: list[dict[str, Any]], tools: list[dict[str, Any]], budget: TurnBudget,
    ) -> Completion:
        self.request_ids.add(budget.request_id)
        return await super().complete(input=input, tools=tools, budget=budget)


def run_cases(
    suite: dict[str, Any], client: TestClient, assert_publication: Callable[[], None], *,
    headers: Mapping[str, str] | None = None,
    results: list[dict[str, Any]] | None = None,
    checkpoint: Callable[[], None] = lambda: None,
) -> list[dict[str, Any]]:
    """Capture actual responses; HTTP delivery and semantic outcomes stay separate."""
    records: list[dict[str, Any]] = [] if results is None else results
    stop_reason: str | None = None
    for case in suite["cases"]:
        history: list[dict[str, str]] = []
        dependency_failed = False
        conversation_id = str(uuid.uuid4())
        request_headers = {**(headers or {}), "x-rockygpt-conversation-id": conversation_id}
        for index, turn in enumerate(case["turns"], 1):
            record: dict[str, Any] = {
                "case_id": case["id"], "turn": index, "student_message": turn["message"],
                "conversation_id": conversation_id, "outcome_review": "unreviewed",
                "status": "planned", "http_success": False, "request": None,
            }
            records.append(record)
            if stop_reason or dependency_failed:
                record["status"] = stop_reason or "skipped_dependent_followup"
                checkpoint()
                continue
            request = {"messages": [*history, {"role": "user", "content": turn["message"]}],
                       "omittedMessages": case.get("omittedMessages", 0)}
            record.update(request=request, started_at=datetime.now(UTC).isoformat())
            try:
                assert_publication()
            except Exception as error:
                stop_reason = ("not_attempted_publication_changed"
                               if isinstance(error, DatasetChanged)
                               else "not_attempted_publication_unavailable")
                record.update(status=stop_reason, error_type=type(error).__name__)
                checkpoint()
                continue
            record["status"] = "in_progress"
            checkpoint()
            started = perf_counter()
            try:
                response = client.post("/v1/chat", json=request, headers=request_headers)
                try:
                    body: Any = response.json()
                except ValueError:
                    body = {"non_json_body": response.text}
                record.update(http_status=response.status_code, body=body,
                              request_id=response.headers.get("x-request-id"),
                              http_success=response.status_code == 200)
                if isinstance(body, dict) and isinstance(body.get("requestId"), str):
                    record["request_id"] = body["requestId"]
                if response.status_code != 200:
                    record["status"] = "http_failure"
                    dependency_failed = True
                elif not isinstance(body, dict) or not isinstance(body.get("answer"), str) \
                        or not body["answer"].strip():
                    record["status"] = "invalid_response"
                    dependency_failed = True
                else:
                    record["status"] = "captured"
                    history = [*request["messages"],
                               {"role": "assistant", "content": body["answer"]}]
            except Exception as error:
                # Never persist provider exception text, headers, URLs, or tracebacks.
                record.update(status="client_error", error_type=type(error).__name__)
                dependency_failed = True
            except KeyboardInterrupt:
                record.update(status="interrupted", error_type="KeyboardInterrupt")
                raise
            finally:
                record.update(elapsed_ms=round((perf_counter() - started) * 1_000, 3),
                              finished_at=datetime.now(UTC).isoformat())
                checkpoint()
    return records



def fill_unattempted(
    suite: dict[str, Any], results: list[dict[str, Any]], reason: str,
) -> None:
    present = {(item["case_id"], item["turn"]) for item in results}
    for case in suite["cases"]:
        for index, turn in enumerate(case["turns"], 1):
            if (case["id"], index) not in present:
                results.append({"case_id": case["id"], "turn": index,
                                "student_message": turn["message"], "status": reason,
                                "outcome_review": "unreviewed", "http_success": False,
                                "request": None})

def accounting(settings: ProviderSettings, request_ids: set[str]) -> dict[str, Any]:
    if not request_ids:
        return {"status": "captured", "operations": [], "settled_nusd": 0, "held_nusd": 0}
    try:
        with psycopg.connect(settings.ledger_url, connect_timeout=5, row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '5000ms'")
            conn.execute("SET LOCAL ROLE brain_development")
            rows = conn.execute(
                "SELECT operation_id,request_id,category,admitted_month,charged_month,"
                "reserved_nusd,cost_nusd,state,usage,error_code,provider_response_id,"
                "returned_model,elapsed_ms,created_at,updated_at FROM brain_ops.operations "
                "WHERE environment='development' AND request_id=ANY(%s) "
                "ORDER BY created_at,operation_id", (sorted(request_ids),),
            ).fetchall()
        settled = sum(row["cost_nusd"] for row in rows if row["state"] == "settled")
        held = sum(row["reserved_nusd"] for row in rows if row["state"] != "settled")
        return {"status": "captured", "operations": rows, "settled_nusd": settled,
                "held_nusd": held, "committed_nusd": settled + held,
                "cost_basis": "Gateway configured conservative pricing; not a provider invoice."}
    except Exception as error:
        return {"status": "unavailable", "error_type": type(error).__name__,
                "operations": None, "settled_nusd": None, "held_nusd": None}


def public_settings(settings: ProviderSettings) -> dict[str, Any]:
    return {"environment": settings.environment, "pricing": settings.prices.metadata(),
            "max_input_bytes": settings.max_input_bytes,
            "max_output_tokens": settings.max_output_tokens,
            "max_turn_nusd": settings.max_turn_nusd}


def fingerprints(cases_path: Path, oracle_path: Path, settings: ProviderSettings) -> dict[str, Any]:
    paths = [path for path in (ROOT / "src").rglob("*") if path.suffix in {".py", ".json"}]
    paths += [Path(__file__), ROOT / "pyproject.toml"]
    sources = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(paths)}
    packages = {}
    for name in ("openai", "fastapi", "starlette", "httpx", "psycopg", "psycopg-pool"):
        packages[name] = importlib.metadata.version(name)
    return {"source_files": sources, "source_sha256": digest(sources),
            "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            "tools_sha256": digest(TOOLS), "settings_sha256": digest(public_settings(settings)),
            "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
            "oracle_sha256": hashlib.sha256(oracle_path.read_bytes()).hexdigest(),
            "packages": packages}


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date, uuid.UUID)):
        return str(value)
    raise TypeError("Unsupported report value")


def save_report(stream: TextIO, report: dict[str, Any]) -> None:
    encoded = json.dumps(report, indent=2, ensure_ascii=False, default=_json_default) + "\n"
    stream.seek(0)
    stream.write(encoded)
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


def summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "planned_turns": len(results),
        "attempted_turns": sum("http_status" in result or result["status"] in
                               {"client_error", "interrupted"} for result in results),
        "http_200_turns": sum(result["http_success"] for result in results),
        "captured_answer_turns": sum(result["status"] == "captured" for result in results),
        "unreviewed_turns": len(results),
        "outcome_review": "unreviewed",
        "quality_conclusion": None,
        "counts_by_capture_status": {status: sum(result["status"] == status for result in results)
                                     for status in sorted({r["status"] for r in results})},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-total-usd", required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required; this command can make paid requests")
    if args.out.exists():
        parser.error("output already exists; preserve it and choose a new path")
    try:
        suite = json.loads(args.cases.read_text())
        oracle = json.loads(args.oracle.read_text())
        settings = ProviderSettings.from_env()
        max_total_nusd = usd_to_nusd(args.max_total_usd)
        planned = preflight(suite, oracle, settings, max_total_nusd)
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url:
            raise CaptureError("DATABASE_URL must already be configured.")
        initial_fingerprints = fingerprints(args.cases, args.oracle, settings)
    except (ConfigurationError, CaptureError) as error:
        parser.error(str(error))
    except Exception as error:
        parser.error(f"Preflight failed ({type(error).__name__}); details withheld")
    report: dict[str, Any] = {
        "kind": "prospective_synthetic_live_capture", "runner_version": RUNNER_VERSION,
        "suite_id": suite.get("suite_id"), "cases_file": args.cases.name,
        "oracle_file": args.oracle.name,
        "synthetic": True, "real_student_traffic": False, "blind_human_study": False,
        "outcome_review": "unreviewed", "started_at": datetime.now(UTC).isoformat(),
        "planned_turns": planned, "max_total_nusd": max_total_nusd,
        "worst_case_admission_nusd": planned * settings.max_turn_nusd,
        "settings": public_settings(settings), "fingerprints_before": initial_fingerprints,
        "publication": {key: oracle[key] for key in ("dataset_version", "identity_hash")},
        "oracle_captured_at": oracle.get("captured_at"),
        "limitations": [
            "Synthetic prospective cases are not real student traffic or a blind study.",
            "HTTP success does not establish grounding, completeness, or student value.",
            "No semantic judgments are generated; independent outcome review is required.",
            "Ledger costs use conservative configured rates, not provider invoice data.",
            "Oracle freshness requires operator review; capture time does not reverify facts.",
        ],
        "turns": [],
    }
    gateway: CaptureGateway | None = None
    exit_code = 0
    # Exclusive creation is the last gate before opening any runtime connection.
    # Checkpoints update only this newly created report; existing reports are never replaced.
    try:
        with args.out.open("x") as stream:
            save_report(stream, report)
            try:
                facts = PinnedFacts(database_url, oracle)
                gateway = CaptureGateway(settings)
                engine = ChatEngine(gateway, facts, max_turn_nusd=settings.max_turn_nusd)
                token = os.environ.get("STAGING_SERVICE_TOKEN", "").strip()
                headers = {"x-rockygpt-environment-token": token} if token else {}
                with TestClient(create_app(engine, environment="development",
                                           service_token=token),
                                raise_server_exceptions=False) as client:
                    ready = client.get("/readiness")
                    report["readiness_status"] = ready.status_code
                    if ready.status_code != 200:
                        raise CaptureError("Runtime readiness failed before capture.")
                    run_cases(suite, client, facts.assert_publication, headers=headers,
                              results=report["turns"],
                              checkpoint=lambda: save_report(stream, report))
            except KeyboardInterrupt:
                report["run_error"] = {"error_type": "KeyboardInterrupt"}
                exit_code = 130
            except Exception as error:
                report["run_error"] = {"error_type": type(error).__name__}
                exit_code = 1
            finally:
                request_ids = {r["request_id"] for r in report["turns"] if r.get("request_id")}
                if gateway is not None:
                    request_ids.update(gateway.request_ids)
                fill_unattempted(suite, report["turns"], "not_attempted_run_failure")
                report["accounting"] = accounting(settings, request_ids)
                report["summary"] = summary(report["turns"])
                report["finished_at"] = datetime.now(UTC).isoformat()
                try:
                    report["fingerprints_after"] = fingerprints(args.cases, args.oracle, settings)
                    report["inputs_unchanged"] = (
                        initial_fingerprints == report["fingerprints_after"]
                    )
                except Exception as error:
                    report["fingerprint_error"] = {"error_type": type(error).__name__}
                    report["inputs_unchanged"] = False
                save_report(stream, report)
    except FileExistsError:
        parser.error("output already exists; no runtime was opened")
    except OSError as error:
        parser.error(f"Report write failed ({type(error).__name__}); details withheld")
    complete_capture = (len(report["turns"]) == planned and report["inputs_unchanged"]
                        and report["accounting"]["status"] == "captured"
                        and all(r["status"] == "captured" for r in report["turns"]))
    print(json.dumps(report["summary"], sort_keys=True))
    return exit_code or (0 if complete_capture else 1)


if __name__ == "__main__":
    raise SystemExit(main())
