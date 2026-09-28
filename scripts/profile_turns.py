"""Where a chat turn spends its time, measured stage by stage.

Three commands:

    probe      Free. Times connections and round trips to the campus database, the ledger
               database, OpenAI and TypeSafe, and the per-request release setup (dataset,
               sources, identity registry). No model call and no ledger write.
    turns      Paid, development only. Runs the fixed synthetic cases in
               docs/routing/cases.json in-process through the same run_turn, gateway and
               ledger the HTTP route uses, and times each stage by wrapping functions:
               connections, queries, ledger writes, each model call, each tool and CPU.
               Nothing in src/ changes.
    summarize  Free. Prints the stage breakdown of a saved `turns` report, or of saved
               Brain HTTP responses (a JSON list of {"seconds", "raw"} items, as
               prod_check.py writes them).

Reports hold timings, counts, token usage and fixed status codes. Host names are reduced
to their cloud region; connection strings and keys are never printed or saved. Answer
text is kept only with --keep-answers (the cases are synthetic).
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import socket
import ssl
import statistics
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

CASES = Path(__file__).parents[1] / "docs/routing/cases.json"
CAMPUS_ZONE = ZoneInfo("America/New_York")
NEON_HOST = re.compile(r"^(?P<endpoint>[a-z0-9-]+?)(?P<pooler>-pooler)?\.(?:c-\d+\.)?"
                       r"(?P<region>[a-z]{2}-[a-z]+-\d)\.aws\.neon\.tech$")


def region_of(host: str | None) -> dict[str, Any]:
    """A host's provider and region, never the host itself."""
    if not host:
        return {"provider": "unknown"}
    if host in {"localhost", "127.0.0.1", "::1"}:
        return {"provider": "local"}
    match = NEON_HOST.match(host)
    if match:
        return {"provider": "neon", "region": f"aws-{match['region']}",
                "pooled": bool(match["pooler"])}
    azure = re.search(r"\.([a-z]+)\.azure\.neon\.tech$", host)
    if azure:
        return {"provider": "neon", "region": f"azure-{azure[1]}",
                "pooled": "-pooler." in host}
    return {"provider": "other"}


def database_host(url: str) -> str | None:
    from psycopg.conninfo import conninfo_to_dict

    host = conninfo_to_dict(url).get("host")
    return str(host) if host else urlsplit(url).hostname


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def spread(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    return {"n": len(values), "median": round(statistics.median(values), 1),
            "p90": round(percentile(values, 0.9), 1), "max": round(max(values), 1),
            "mean": round(statistics.fmean(values), 1)}


# ---------------------------------------------------------------------------------------
# probe: free network and setup timings


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def probe_database(url: str, label: str, *, campus: bool) -> dict[str, Any]:
    import certifi
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    host = database_host(url)
    port = int(conninfo_to_dict(url).get("port") or 5432)
    result: dict[str, Any] = {"database": label, **region_of(host)}
    assert host is not None
    started = time.perf_counter()
    address = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)[0][4]
    result["dnsMs"] = _ms(started)
    started = time.perf_counter()
    with socket.create_connection((str(address[0]), int(address[1])), timeout=5):
        result["tcpConnectMs"] = _ms(started)
    options: dict[str, Any] = conninfo_to_dict(url)
    options.pop("connect_timeout", None)
    if "sslrootcert" not in options and not os.getenv("PGSSLROOTCERT"):
        options["sslrootcert"] = certifi.where()
    started = time.perf_counter()
    with psycopg.connect(**options, autocommit=True, connect_timeout=10) as conn:
        result["connectMs"] = _ms(started)
        trips = []
        for _ in range(7):
            started = time.perf_counter()
            conn.execute("SELECT 1").fetchall()
            trips.append(_ms(started))
        result["selectOneMs"] = spread(trips)
        if campus:
            # The shape CampusData._fetch uses for every query: BEGIN READ ONLY,
            # set_config, the query and COMMIT, one round trip each.
            conn.read_only = True
            fetches, piped = [], []
            for _ in range(5):
                started = time.perf_counter()
                with conn.transaction(), conn.cursor() as cursor:
                    cursor.execute("SELECT set_config('statement_timeout', %s, true)", ("8000",))
                    cursor.execute("SELECT 1")
                    cursor.fetchall()
                fetches.append(_ms(started))
            for _ in range(5):
                started = time.perf_counter()
                with conn.pipeline(), conn.transaction(), conn.cursor() as cursor:
                    cursor.execute("SELECT set_config('statement_timeout', %s, true)", ("8000",))
                    cursor.execute("SELECT 1")
                    cursor.fetchall()
                piped.append(_ms(started))
            result["fetchPatternMs"] = spread(fetches)
            result["pipelinedFetchMs"] = spread(piped)
    return result


def probe_release(url: str) -> dict[str, Any]:
    """What every request repeats today before its first tool: connect, dataset, sources,
    fingerprint, the identity registry download and its validation."""
    from rockygpt_brain.retrieval.data import CampusData
    from rockygpt_brain.retrieval.profiles import IdentityRegistry

    runs = []
    for _ in range(3):
        data = CampusData(url, datetime.now(CAMPUS_ZONE))
        try:
            run: dict[str, float | int] = {}
            started = time.perf_counter()
            data._ensure_loaded()
            run["ensureLoadedMs"] = _ms(started)
            started = time.perf_counter()
            data.release_fingerprint()
            run["fingerprintMs"] = _ms(started)
            for key in ("campus-identities", "campus-identity-coverage", "search-vocabulary"):
                started = time.perf_counter()
                payload = data._artifact(key)
                run[f"artifact:{key}:fetchMs"] = _ms(started)
                run[f"artifact:{key}:bytes"] = len(json.dumps(payload, ensure_ascii=False)
                                                   .encode()) if payload is not None else 0
            payload = data._artifact("campus-identities")
            if payload is not None:
                cpu = time.process_time()
                started = time.perf_counter()
                registry = IdentityRegistry.model_validate(payload)
                run["identityValidateMs"] = _ms(started)
                run["identityValidateCpuMs"] = round((time.process_time() - cpu) * 1000, 1)
                run["identityEntities"] = len(registry.entities)
            runs.append(run)
        finally:
            data.close()
    keys = dict.fromkeys(key for run in runs for key in run)
    return {key: spread([float(run[key]) for run in runs if key in run]) for key in keys}


def probe_https(url: str, label: str) -> dict[str, Any]:
    """New connection (DNS, TCP, TLS) versus a reused one, with an unauthenticated GET."""
    import httpx

    parts = urlsplit(url)
    assert parts.hostname is not None
    host = parts.hostname
    result: dict[str, Any] = {"service": label}
    tls, fresh, reused = [], [], []
    context = ssl.create_default_context()
    for _ in range(3):
        started = time.perf_counter()
        with socket.create_connection((host, 443), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                tls.append(_ms(started))
    for _ in range(3):
        started = time.perf_counter()
        with httpx.Client(timeout=10, trust_env=True) as client:
            client.get(url)
        fresh.append(_ms(started))
    with httpx.Client(timeout=10, trust_env=True) as client:
        client.get(url)
        for _ in range(5):
            started = time.perf_counter()
            client.get(url)
            reused.append(_ms(started))
    result.update(tcpTlsHandshakeMs=spread(tls), newClientRequestMs=spread(fresh),
                  reusedConnectionRequestMs=spread(reused))
    return result


def probe(output: Path | None) -> dict[str, Any]:
    report: dict[str, Any] = {"kind": "probe", "startedAt": datetime.now(CAMPUS_ZONE).isoformat(),
                              "databases": [], "https": []}
    for variable, label, campus in (("DATABASE_URL", "campus", True),
                                    ("BRAIN_LEDGER_DATABASE_URL", "ledger", False)):
        url = os.getenv(variable)
        if not url:
            report["databases"].append({"database": label, "status": "not_configured"})
            continue
        try:
            report["databases"].append(probe_database(url, label, campus=campus))
        except Exception as error:  # A probe reports the failure class, never the message.
            report["databases"].append({"database": label, "error": type(error).__name__})
    campus_url = os.getenv("DATABASE_URL")
    if campus_url:
        try:
            report["releaseSetup"] = probe_release(campus_url)
        except Exception as error:
            report["releaseSetup"] = {"error": type(error).__name__}
    for url, label in (("https://api.openai.com/v1/models", "openai"),
                       ("https://api.typesafe.ai/v1/systemone", "typesafe")):
        try:
            report["https"].append(probe_https(url, label))
        except Exception as error:
            report["https"].append({"service": label, "error": type(error).__name__})
    text = json.dumps(report, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n")
    print(text)
    return report


# ---------------------------------------------------------------------------------------
# turns: an instrumented, paid profile


class Recorder:
    """Accumulates wall time and counts per stage for the current turn."""

    def __init__(self) -> None:
        self.turn: dict[str, Any] | None = None
        self.ledger_url = ""

    def add(self, stage: str, elapsed_ms: float, **extra: Any) -> None:
        if self.turn is None:
            return
        stages = self.turn.setdefault("stages", {})
        entry = stages.setdefault(stage, {"ms": 0.0, "count": 0})
        entry["ms"] = round(entry["ms"] + elapsed_ms, 1)
        entry["count"] += 1
        for key, value in extra.items():
            entry.setdefault(key, []).append(value)


RECORDER = Recorder()


def wrap(owner: Any, name: str, stage: str | Callable[..., str]) -> None:
    original = getattr(owner, name)

    @functools.wraps(original)
    def timed(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            label = stage(*args, **kwargs) if callable(stage) else stage
            RECORDER.add(label, (time.perf_counter() - started) * 1000)

    setattr(owner, name, timed)


def instrument() -> None:
    import psycopg
    from psycopg_pool import ConnectionPool

    from rockygpt_brain.core import engine, provider, reviewer, routing
    from rockygpt_brain.governance import accounting
    from rockygpt_brain.retrieval import data, profiles

    def connect_label(*args: Any, **kwargs: Any) -> str:
        url = args[0] if args else kwargs.get("conninfo", "")
        return "db.connect.ledger" if url and url == RECORDER.ledger_url else "db.connect.campus"

    wrap(psycopg, "connect", connect_label)
    # The ledger borrows pooled connections: a check round trip, or a new connection
    # when none is idle.
    wrap(ConnectionPool, "getconn", "db.pool.ledger")
    wrap(data.CampusData, "_fetch", "db.campus.query")
    wrap(data.CampusData, "_ensure_loaded", "setup.campus.ensureLoaded")
    wrap(data.CampusData, "release_fingerprint", "setup.campus.fingerprint")

    original_artifact = data.CampusData._artifact

    def artifact(self: Any, key: str) -> Any:
        cached = key in self._artifacts
        started = time.perf_counter()
        try:
            return original_artifact(self, key)
        finally:
            if not cached:
                RECORDER.add(f"setup.artifact.{key}", (time.perf_counter() - started) * 1000)

    data.CampusData._artifact = artifact  # type: ignore[method-assign]
    for method in ("readiness", "reserve", "settle", "uncertain", "record_turn", "pause"):
        wrap(accounting.PostgresLedger, method, f"ledger.{method}")
    wrap(provider.OpenAIProvider, "create", "model.openai")
    wrap(provider.JevProvider, "create", "model.jev")
    wrap(provider.PaidGateway, "create",
         lambda *args, **kwargs: f"gateway.{kwargs.get('category', 'unknown')}")
    for method in ("search", "read", "lookup_contact", "lookup_profile", "lookup_entity"):
        wrap(data.CampusData, method, f"tool.{method}")
    for module, names in ((engine, ("bounded_result", "tool_result_wire", "render_answer",
                                    "input_bound", "graph_first", "route_request",
                                    "review_answer", "combine_exact")),
                          (provider, ("input_bound",)),
                          (routing, ("shortlist", "input_bound")),
                          (reviewer, ("compact_records",))):
        for name in names:
            wrap(module, name, f"code.{module.__name__.rsplit('.', 1)[-1]}.{name}")
    original_validate = profiles.IdentityRegistry.model_validate

    def validate(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return original_validate(*args, **kwargs)
        finally:
            RECORDER.add("code.identityRegistry.validate", (time.perf_counter() - started) * 1000)

    profiles.IdentityRegistry.model_validate = validate  # type: ignore[method-assign]


def trace_summary(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("tool", "status", "result_count", "total_matches", "truncated", "reason", "elapsed_ms")
    return [{key: entry.get(key) for key in keys} for entry in trace]


def profile_case(case: dict[str, Any], mode: str, now: datetime,
                 keep_answers: bool) -> dict[str, Any]:
    from rockygpt_brain.config import RELEASE, load_deployment  # noqa: F401 (model name only)
    from rockygpt_brain.contracts import ChatMessage
    from rockygpt_brain.core.engine import run_turn
    from rockygpt_brain.core.provider import open_gateway
    from rockygpt_brain.governance.accounting import PaidCallError
    from rockygpt_brain.retrieval.data import CampusData

    deployment = load_deployment().model_copy(update={"routing_mode": mode})
    RECORDER.ledger_url = deployment.ledger_url
    turn: dict[str, Any] = {"id": case["id"], "mode": mode, "routes": case["routes"]}
    RECORDER.turn = turn
    cpu = time.process_time()
    started = time.perf_counter()
    data = CampusData(os.environ["DATABASE_URL"], now)
    try:
        with open_gateway(deployment, "profile-" + str(uuid4())) as gateway:
            turn["gatewayOpenMs"] = _ms(started)
            try:
                response = run_turn(
                    [ChatMessage.model_validate(message) for message in case["messages"]],
                    client=gateway, data=data, model=RELEASE.model, now=now,
                    routing_client=gateway if mode != "off" else None,
                    routing_mode=mode,  # type: ignore[arg-type]
                    explain_rejections=True,
                )
                metrics = response["metrics"]
                turn.update(
                    status=response["status"],
                    responseMode=metrics.get("responseMode"),
                    fallbackReason=metrics.get("fallbackReason"),
                    validationFailures=metrics.get("validationFailures", []),
                    reviewRejectionCount=len(metrics.get("reviewRejections", [])),
                    routing=metrics.get("routing"),
                    graphFirst=metrics.get("graphFirst", False),
                    engineElapsedMs=response["elapsedMs"],
                    trace=trace_summary(response["trace"]),
                    answerChars=len(response["answer"]),
                    citations=len(response["citations"]),
                )
                if keep_answers:
                    turn["answer"] = response["answer"]
            except PaidCallError as error:
                turn["error"] = error.code
            except Exception as error:
                turn["error"] = type(error).__name__
            finally:
                turn["calls"] = [
                    {key: call.get(key) for key in (
                        "category", "elapsedMs", "input_tokens", "cached_input_tokens",
                        "output_tokens", "reasoning_tokens", "costNusd", "error")}
                    for call in gateway.usage.calls
                ]
                finished = time.perf_counter()
                gateway.finish({"evaluation": "profile-turns", "mode": mode,
                                "status": turn.get("status", "unavailable")})
                turn["finishMs"] = _ms(finished)
    finally:
        data.close()
        turn["elapsedMs"] = _ms(started)
        turn["cpuMs"] = round((time.process_time() - cpu) * 1000, 1)
        RECORDER.turn = None
    return turn


EFFORTS = ("none", "minimal", "low", "medium", "high")


def apply_efforts(draft: str | None, continuation: str | None, review: str | None) -> Any:
    """Experiment only: the same release with other reasoning efforts, in this process.

    The gateway sets each call's effort from its release, so the gateway default changes
    too. The configuration hash in the report changes with it, so the environment's
    expected hash is checked against the unchanged code first and then set aside.
    """
    from rockygpt_brain import config
    from rockygpt_brain.core import engine, provider, reviewer

    updates = {key: value for key, value in (("draft_reasoning", draft),
                                             ("continuation_reasoning", continuation),
                                             ("review_reasoning", review)) if value}
    if not updates:
        return config.RELEASE
    expected = os.environ.pop("BRAIN_EXPECTED_CONFIG_HASH", None)
    if expected and expected != config.configuration_hash():
        raise SystemExit("This checkout is not the release the environment expects")
    release = config.RELEASE.model_copy(update=updates)
    config.RELEASE = release
    engine.RELEASE = release  # type: ignore[attr-defined]
    reviewer.RELEASE = release  # type: ignore[attr-defined]
    defaults = provider.PaidGateway.__init__.__kwdefaults__
    assert defaults is not None
    defaults["release"] = release
    return release


def efforts(release: Any) -> dict[str, str]:
    return {"draft": release.draft_reasoning, "continuation": release.continuation_reasoning,
            "review": release.review_reasoning}


def turns(output: Path, *, repeats: int, mode: str, only: list[str] | None,
          keep_answers: bool) -> dict[str, Any]:
    from rockygpt_brain import config

    if os.getenv("BRAIN_ENVIRONMENT") != "development":
        raise SystemExit("Profiling makes paid calls and requires BRAIN_ENVIRONMENT=development")
    instrument()
    RELEASE, configuration_hash = config.RELEASE, config.configuration_hash
    cases = json.loads(CASES.read_text())["cases"]
    if only:
        cases = [case for case in cases if case["id"] in only]
    report: dict[str, Any] = {
        "kind": "turns", "status": "incomplete", "mode": mode, "repeats": repeats,
        "configurationHash": configuration_hash(), "release": RELEASE.version,
        "efforts": efforts(RELEASE),
        "startedAt": datetime.now(CAMPUS_ZONE).isoformat(), "turns": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    for repeat in range(repeats):
        for case in cases:
            turn = profile_case(case, mode, datetime.now(CAMPUS_ZONE), keep_answers)
            turn["repeat"] = repeat
            report["turns"].append(turn)
            output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({"case": case["id"], "repeat": repeat,
                              "elapsedMs": turn["elapsedMs"], "status": turn.get("status"),
                              "error": turn.get("error")}), flush=True)
            if turn.get("error") in {"budget_exhausted", "accounting_unavailable",
                                     "accounting_paused", "model_quota_exhausted"}:
                output.write_text(json.dumps(report, indent=2) + "\n")
                raise SystemExit(f"Stopped: {turn['error']}")
    report["status"] = "complete"
    report["summary"] = summarize_turns(report["turns"])
    output.write_text(json.dumps(report, indent=2) + "\n")
    return report


def reviews(corpus: Path, output: Path, *, repeats: int) -> dict[str, Any]:
    """The evidence review alone on rockygpt-evals' labeled evidence-gate cases: whether
    each verdict matches the label, and how long and how many tokens each review takes."""
    import datetime as dt

    from rockygpt_brain import config
    from rockygpt_brain.contracts import Answer, ChatMessage
    from rockygpt_brain.core.provider import open_gateway
    from rockygpt_brain.core.reviewer import review_answer
    from rockygpt_brain.governance.accounting import PaidCallError

    deployment = config.load_deployment()
    if deployment.environment != "development":
        raise SystemExit("Review checks make paid calls and require BRAIN_ENVIRONMENT=development")
    cases = json.loads(corpus.read_text())["cases"]
    report: dict[str, Any] = {
        "kind": "reviews", "status": "incomplete", "repeats": repeats,
        "efforts": efforts(config.RELEASE), "configurationHash": config.configuration_hash(),
        "startedAt": datetime.now(CAMPUS_ZONE).isoformat(), "results": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    for repeat in range(repeats):
        for case in cases:
            item: dict[str, Any] = {"id": case["id"], "repeat": repeat,
                                    "expectedSupported": case["expected"]["supported"]}
            started = time.perf_counter()
            try:
                with open_gateway(deployment, "review-check-" + str(uuid4())) as client:
                    try:
                        review = review_answer(
                            Answer.model_validate(case["candidate"]),
                            messages=[ChatMessage.model_validate(message)
                                      for message in case["messages"]],
                            evidence={record["id"]: record for record in case["evidence"]},
                            client=client, model=config.RELEASE.model,
                            now=dt.datetime.fromisoformat(case["campus_time"]),
                            timeout=config.RELEASE.turn_seconds,
                            retrievals=case.get("search_coverage"),
                        )
                        verdicts = {part.part_index: part.verdict == "supported"
                                    for part in review.parts}
                        supported = all(verdicts.values())
                        item.update(supported=supported, passed=(
                            supported == case["expected"]["supported"] and all(
                                verdicts[expected["part_index"]] == expected["supported"]
                                for expected in case["expected"]["parts"])))
                    except PaidCallError as error:
                        item["error"] = error.code
                    except Exception as error:
                        item["error"] = type(error).__name__
                    finally:
                        item["calls"] = [
                            {key: call.get(key) for key in (
                                "elapsedMs", "input_tokens", "cached_input_tokens",
                                "output_tokens", "reasoning_tokens", "costNusd")}
                            for call in client.usage.calls
                        ]
                        client.finish({"evaluation": "profile-reviews",
                                       "status": "passed" if item.get("passed") else "failed"})
            except PaidCallError as error:
                item["error"] = error.code
            item["elapsedMs"] = _ms(started)
            report["results"].append(item)
            output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({key: item.get(key) for key in (
                "id", "repeat", "passed", "error", "elapsedMs")}), flush=True)
            if item.get("error") in {"budget_exhausted", "accounting_unavailable",
                                     "accounting_paused", "model_quota_exhausted"}:
                raise SystemExit(f"Stopped: {item['error']}")
    report["status"] = "complete"
    report["summary"] = summarize_reviews(report["results"])
    output.write_text(json.dumps(report, indent=2) + "\n")
    return report


def summarize_reviews(results: list[dict[str, Any]]) -> dict[str, Any]:
    ran = [item for item in results if "error" not in item]
    bad = [item for item in ran if not item["expectedSupported"]]
    good = [item for item in ran if item["expectedSupported"]]
    calls = [call for item in ran for call in item.get("calls", [])]
    return {
        "reviews": len(results), "errors": len(results) - len(ran),
        "passed": sum(bool(item.get("passed")) for item in ran),
        "badAnswersRejected": f"{sum(not item['supported'] for item in bad)}/{len(bad)}",
        "goodAnswersAccepted": f"{sum(bool(item['supported']) for item in good)}/{len(good)}",
        "failedCases": sorted({item["id"] for item in ran if not item.get("passed")}),
        "elapsedMs": spread([item["elapsedMs"] for item in ran]),
        "modelMs": spread([float(call.get("elapsedMs") or 0) for call in calls]),
        "reasoningTokens": spread([float(call.get("reasoning_tokens") or 0) for call in calls]),
        "costNusd": spread([float(call.get("costNusd") or 0) for call in calls]),
    }


# ---------------------------------------------------------------------------------------
# summarize


def summarize_turns(items: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [turn for turn in items if "error" not in turn]
    stage_names = dict.fromkeys(name for turn in ok for name in turn.get("stages", {}))
    stages = {
        name: {**spread([turn.get("stages", {}).get(name, {}).get("ms", 0.0) for turn in ok]),
               "callsPerTurn": round(statistics.fmean(
                   turn.get("stages", {}).get(name, {}).get("count", 0) for turn in ok), 2)}
        for name in sorted(stage_names)
    } if ok else {}
    by_call: dict[str, list[dict[str, Any]]] = {}
    for turn in ok:
        for call in turn.get("calls", []):
            by_call.setdefault(str(call.get("category")), []).append(call)
    calls = {
        category: {
            "elapsedMs": spread([float(call.get("elapsedMs") or 0) for call in group]),
            "inputTokens": spread([float(call.get("input_tokens") or 0) for call in group]),
            "cachedShare": round(sum(call.get("cached_input_tokens") or 0 for call in group)
                                 / max(1, sum(call.get("input_tokens") or 0 for call in group)), 3),
            "outputTokens": spread([float(call.get("output_tokens") or 0) for call in group]),
            "reasoningTokens": spread([float(call.get("reasoning_tokens") or 0)
                                       for call in group]),
            "costNusd": spread([float(call.get("costNusd") or 0) for call in group]),
        }
        for category, group in by_call.items()
    }
    modes: dict[str, list[float]] = {}
    for turn in ok:
        modes.setdefault(str(turn.get("responseMode")), []).append(turn["elapsedMs"])
    return {
        "turns": len(items), "errors": len(items) - len(ok),
        "elapsedMs": spread([turn["elapsedMs"] for turn in ok]),
        "cpuMs": spread([turn["cpuMs"] for turn in ok]),
        "costNusdPerTurn": spread([float(sum(call.get("costNusd") or 0
                                             for call in turn.get("calls", []))) for turn in ok]),
        "byResponseMode": {mode: spread(values) for mode, values in sorted(modes.items())},
        "stages": stages,
        "modelCalls": calls,
        "statuses": {status: sum(turn.get("status") == status for turn in ok)
                     for status in dict.fromkeys(str(turn.get("status")) for turn in ok)},
    }


def summarize_responses(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Brain HTTP responses: client seconds versus the Brain's own elapsed and model time."""
    rows = []
    for item in items:
        try:
            body = json.loads(item["raw"])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        metrics = body.get("metrics") or {}
        if "elapsedMs" not in body:
            continue
        model = sum(float(metrics.get(key) or 0)
                    for key in ("draftModelMs", "reviewModelMs", "routingModelMs"))
        retrieval = float(metrics.get("retrievalMs") or 0)
        rows.append({
            "clientMs": float(item.get("seconds") or 0) * 1000,
            "brainMs": float(body["elapsedMs"]),
            "draftModelMs": float(metrics.get("draftModelMs") or 0),
            "reviewModelMs": float(metrics.get("reviewModelMs") or 0),
            "routingModelMs": float(metrics.get("routingModelMs") or 0),
            "retrievalMs": retrieval,
            "otherBrainMs": float(body["elapsedMs"]) - model - retrieval,
            "outsideBrainMs": float(item.get("seconds") or 0) * 1000 - float(body["elapsedMs"]),
            "modelCalls": metrics.get("modelCalls"),
            "responseMode": metrics.get("responseMode"),
        })
    keys = ("clientMs", "brainMs", "draftModelMs", "reviewModelMs", "routingModelMs",
            "retrievalMs", "otherBrainMs", "outsideBrainMs")
    return {"responses": len(rows),
            **{key: spread([float(row[key] or 0) for row in rows]) for key in keys},
            "rows": rows}


def summarize(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text())
    if isinstance(loaded, dict) and loaded.get("kind") == "turns":
        result = summarize_turns(loaded["turns"])
    elif isinstance(loaded, dict) and loaded.get("kind") == "reviews":
        result = summarize_reviews(loaded["results"])
    elif isinstance(loaded, list):
        result = summarize_responses(loaded)
    else:
        raise SystemExit("Unrecognized report")
    print(json.dumps(result, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    probe_parser = commands.add_parser("probe")
    probe_parser.add_argument("--output", type=Path)
    turns_parser = commands.add_parser("turns")
    turns_parser.add_argument("--output", type=Path, required=True)
    turns_parser.add_argument("--repeats", type=int, default=1)
    turns_parser.add_argument("--mode", choices=("off", "shadow", "active"),
                              default=os.getenv("BRAIN_ROUTING_MODE") or "off")
    turns_parser.add_argument("--only", nargs="*", help="Case IDs from docs/routing/cases.json")
    turns_parser.add_argument("--keep-answers", action="store_true")
    review_parser = commands.add_parser("reviews")
    review_parser.add_argument("--corpus", type=Path, required=True,
                               help="rockygpt-evals brain-reset/evidence-gate-cases.json")
    review_parser.add_argument("--output", type=Path, required=True)
    review_parser.add_argument("--repeats", type=int, default=1)
    summarize_parser = commands.add_parser("summarize")
    summarize_parser.add_argument("report", type=Path)
    for command in (probe_parser, turns_parser, review_parser):
        command.add_argument("--env-file", type=Path,
                             help="Settings file to load (default: .env in the working directory)")
    for command in (turns_parser, review_parser):
        for name in ("draft", "continuation", "review"):
            command.add_argument(f"--{name}-effort", choices=EFFORTS,
                                 help="Experiment: override this reasoning effort")
    args = parser.parse_args()
    if args.command in {"probe", "turns", "reviews"}:
        from dotenv import load_dotenv

        load_dotenv(args.env_file)
    if args.command in {"turns", "reviews"}:
        if not 1 <= args.repeats <= 5:
            parser.error("--repeats must be between 1 and 5")
        if args.output.exists():
            parser.error("--output exists; reports are never overwritten")
        apply_efforts(args.draft_effort, args.continuation_effort, args.review_effort)
    if args.command == "probe":
        probe(args.output)
    elif args.command == "reviews":
        print(json.dumps(reviews(args.corpus, args.output, repeats=args.repeats)["summary"],
                         indent=2))
    elif args.command == "turns":
        report = turns(args.output, repeats=args.repeats, mode=args.mode, only=args.only,
                       keep_answers=args.keep_answers)
        print(json.dumps(report["summary"], indent=2))
    else:
        summarize(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
