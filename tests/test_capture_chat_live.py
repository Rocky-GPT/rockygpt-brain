"""Runner mechanics use ordinary fixtures, never prospective holdout cases."""

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from capture_chat_live import (
    CaptureError,
    PinnedFacts,
    fill_unattempted,
    main,
    preflight,
    public_settings,
    run_cases,
    summary,
    usd_to_nusd,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from rockygpt_brain.retrieval import DatasetChanged, PostgresEntityFacts
from rockygpt_brain.retrieval.entity_facts import Snapshot
from rockygpt_brain.settings import Prices, ProviderSettings

SETTINGS = ProviderSettings(
    "development", "fake-secret-key", "fake-project", "fake-secret-url",
    Prices("test-model", 125, 500, 10, datetime(2099, 1, 1, tzinfo=UTC)),
)
ORACLE = {"dataset_version": "fixture-release", "identity_hash": "fixture-identity"}


def suite(*counts: int, omitted: int = 0) -> dict[str, Any]:
    return {"schema_version": 1, "method_metadata": {
        "synthetic": True, "real_student_traffic": False,
        "execution_protocol": {"student_turn_count": sum(counts)},
    }, "cases": [{"id": f"case-{index}", "omittedMessages": omitted, "turns": [
        {"message": f"synthetic question {index}.{turn}"} for turn in range(count)
    ]} for index, count in enumerate(counts)]}


def app_for(responses: list[Any], seen: list[dict[str, Any]]) -> FastAPI:
    app = FastAPI()

    @app.get("/readiness")
    def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.post("/v1/chat")
    async def chat(request: Request) -> JSONResponse:
        seen.append({"body": await request.json(),
                     "conversation": request.headers["x-rockygpt-conversation-id"]})
        item = responses[len(seen) - 1]
        if isinstance(item, Exception):
            raise item
        code, body = item
        return JSONResponse(body, status_code=code, headers={"X-Request-Id": "fixture-request"})

    return app


def answer(text: str = "actual fixture answer") -> tuple[int, dict[str, str]]:
    return 200, {"answer": text, "requestId": "fixture-request", "status": "answered"}


def test_capture_replays_actual_answers_keeps_omissions_and_isolates_cases() -> None:
    seen: list[dict[str, Any]] = []
    pins: list[bool] = []
    with TestClient(app_for([answer("first actual answer"), answer(), answer()], seen)) as client:
        results = run_cases(suite(2, 1, omitted=7), client, lambda: pins.append(True))
    assert len(pins) == 3
    assert seen[1]["body"] == {"messages": [
        {"role": "user", "content": "synthetic question 0.0"},
        {"role": "assistant", "content": "first actual answer"},
        {"role": "user", "content": "synthetic question 0.1"},
    ], "omittedMessages": 7}
    assert len(seen[2]["body"]["messages"]) == 1
    assert seen[0]["conversation"] == seen[1]["conversation"] != seen[2]["conversation"]
    assert all(item["outcome_review"] == "unreviewed" for item in results)
    totals = summary(results)
    assert totals["http_200_turns"] == 3
    assert totals["quality_conclusion"] is None
    assert "passed" not in totals


def test_http_failure_skips_dependent_followups_and_continues_independent_case() -> None:
    seen: list[dict[str, Any]] = []
    responses = [(503, {"error": {"code": "model_timeout"}}), answer()]
    with TestClient(app_for(responses, seen)) as client:
        results = run_cases(suite(2, 1), client, lambda: None)
    assert [result["status"] for result in results] == [
        "http_failure", "skipped_dependent_followup", "captured",
    ]
    assert len(seen) == 2
    assert results[1]["request"] is None
    assert summary(results)["attempted_turns"] == 2


def test_exception_captures_only_class_then_continues_independent_case() -> None:
    seen: list[dict[str, Any]] = []
    responses = [RuntimeError("private-key-and-provider-body"), answer()]
    with TestClient(app_for(responses, seen)) as client:
        results = run_cases(suite(2, 1), client, lambda: None)
    assert results[0]["error_type"] == "RuntimeError"
    assert "private-key" not in json.dumps(results)
    assert results[1]["status"] == "skipped_dependent_followup"
    assert results[2]["status"] == "captured"


def test_200_without_answer_is_not_a_complete_capture_or_followup_context() -> None:
    seen: list[dict[str, Any]] = []
    with TestClient(app_for([(200, {"status": "answered"}), answer()], seen)) as client:
        results = run_cases(suite(2, 1), client, lambda: None)
    assert results[0]["status"] == "invalid_response"
    assert results[1]["status"] == "skipped_dependent_followup"  # No answer, no follow-up context.
    assert len(seen) == 2  # The first turn and the independent second case only.
    assert results[0]["http_success"] is True
    assert summary(results)["http_200_turns"] == 2
    assert summary(results)["captured_answer_turns"] == 1


def test_publication_change_stops_all_remaining_turns_before_http() -> None:
    seen: list[dict[str, Any]] = []
    calls = 0

    def pin() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise DatasetChanged("private database details")

    with TestClient(app_for([answer()], seen)) as client:
        results = run_cases(suite(2, 1), client, pin)
    assert len(seen) == 1
    assert [row["status"] for row in results[1:]] == ["not_attempted_publication_changed"] * 2
    assert "private database" not in json.dumps(results)


def test_each_actual_fact_snapshot_is_pinned_too(monkeypatch: pytest.MonkeyPatch) -> None:
    release = {"version": "fixture-release", "identity": "fixture-identity"}

    @contextmanager
    def snapshot(_: Any) -> Any:
        yield Snapshot(release["version"], release["identity"], [], lambda _: [], [])

    monkeypatch.setattr(PostgresEntityFacts, "snapshot", snapshot)
    facts = PinnedFacts("unused", ORACLE)
    facts.assert_publication()
    for key, changed in (("version", "changed-after-preflight"),
                         ("identity", "changed-identity-registry")):
        original = release[key]
        release[key] = changed
        with pytest.raises(DatasetChanged):
            with facts.snapshot():
                pytest.fail("A changed publication must not be read")
        release[key] = original


def test_run_allowance_uses_all_planned_turns_at_conservative_maximum() -> None:
    assert preflight(suite(20), ORACLE, SETTINGS, usd_to_nusd("0.50")) == 20
    with pytest.raises(CaptureError, match="exceed"):
        preflight(suite(20), ORACLE, SETTINGS, usd_to_nusd("0.499999999"))
    with pytest.raises(CaptureError, match="development"):
        preflight(suite(1), ORACLE, replace(SETTINGS, environment="production"), 25_000_000)


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "0.0000000001", "nonsense"])
def test_invalid_spending_ceiling_is_rejected(value: str) -> None:
    with pytest.raises(CaptureError):
        usd_to_nusd(value)


def test_invalid_counts_and_oracle_dates_are_rejected() -> None:
    cases = suite(2)
    cases["method_metadata"]["execution_protocol"]["student_turn_count"] = 1
    with pytest.raises(CaptureError, match="count"):
        preflight(cases, ORACLE, SETTINGS, 50_000_000)
    for captured_at in ("invalid", "2099-01-01T00:00:00Z", "2026-01-01"):
        with pytest.raises(CaptureError, match="captured_at"):
            preflight(suite(1), {**ORACLE, "captured_at": captured_at}, SETTINGS, 25_000_000)


def test_public_settings_never_include_credentials() -> None:
    serialized = json.dumps(public_settings(SETTINGS))
    assert "secret" not in serialized
    assert "project" not in serialized


def runner_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    cases, oracle, out = (tmp_path / name for name in ("cases.json", "oracle.json", "capture.json"))
    cases.write_text(json.dumps(suite(1)))
    oracle.write_text(json.dumps(ORACLE))
    return cases, oracle, out


def arm_tripwires(monkeypatch: pytest.MonkeyPatch, *, settings: ProviderSettings | None) -> None:
    """Every paid or database-touching step fails the test if it is ever reached."""
    import capture_chat_live as runner

    def never(*_: Any, **__: Any) -> Any:
        pytest.fail("Refusals must happen before any runtime is built")

    monkeypatch.setattr(runner, "PinnedFacts", never)
    monkeypatch.setattr(runner, "CaptureGateway", never)
    if settings is None:
        monkeypatch.setattr(ProviderSettings, "from_env", never)
    else:
        monkeypatch.setattr(ProviderSettings, "from_env", lambda: settings)
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")


def test_a_paid_run_needs_live_and_never_replaces_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    arm_tripwires(monkeypatch, settings=None)
    cases, oracle, out = runner_files(tmp_path)
    args = ["--cases", str(cases), "--oracle", str(oracle), "--out", str(out),
            "--max-total-usd", "0.50"]
    with pytest.raises(SystemExit) as missing_live:
        main(args)
    assert missing_live.value.code == 2
    assert "--live is required" in capsys.readouterr().err
    assert not out.exists()
    out.write_text("preserved evidence")
    with pytest.raises(SystemExit) as existing:
        main(["--live", *args])
    assert existing.value.code == 2
    assert "output already exists" in capsys.readouterr().err
    assert out.read_text() == "preserved evidence"


def test_the_run_ceiling_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    arm_tripwires(monkeypatch, settings=SETTINGS)
    cases, oracle, out = runner_files(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(["--live", "--cases", str(cases), "--oracle", str(oracle), "--out", str(out)])
    assert caught.value.code == 2
    assert "--max-total-usd" in capsys.readouterr().err
    assert not out.exists()


def test_production_and_over_ceiling_runs_are_refused_by_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    cases, oracle, out = runner_files(tmp_path)
    base = ["--live", "--cases", str(cases), "--oracle", str(oracle), "--out", str(out)]
    arm_tripwires(monkeypatch, settings=replace(SETTINGS, environment="production"))
    with pytest.raises(SystemExit) as production:
        main([*base, "--max-total-usd", "0.50"])
    assert production.value.code == 2
    assert "restricted to the development environment" in capsys.readouterr().err
    assert not out.exists()
    arm_tripwires(monkeypatch, settings=SETTINGS)
    just_under = SETTINGS.max_turn_nusd - 1  # One planned turn at its maximum allowance.
    with pytest.raises(SystemExit) as too_costly:
        main([*base, "--max-total-usd", str(just_under / 1_000_000_000)])
    assert too_costly.value.code == 2
    assert "exceed the run ceiling" in capsys.readouterr().err
    assert not out.exists()


def test_only_an_explicitly_synthetic_suite_is_accepted() -> None:
    for change in ({"synthetic": False}, {"real_student_traffic": True}, {"synthetic": None}):
        cases = suite(1)
        cases["method_metadata"].update(change)
        with pytest.raises(CaptureError, match="synthetic"):
            preflight(cases, ORACLE, SETTINGS, 25_000_000)


def test_unattempted_turns_remain_visible_after_interruption() -> None:
    cases = suite(2, 1)
    results: list[dict[str, Any]] = [{"case_id": "case-0", "turn": 1, "status": "interrupted",
                                    "outcome_review": "unreviewed", "http_success": False}]
    fill_unattempted(cases, results, "not_attempted_interrupted")
    assert len(results) == 3
    assert results[-1]["status"] == "not_attempted_interrupted"
    assert summary(results)["planned_turns"] == 3


def test_main_writes_unreviewed_capture_and_records_accounting_without_grading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import capture_chat_live as runner

    seen: list[dict[str, Any]] = []
    cases, oracle, out = (tmp_path / name for name in ("cases.json", "oracle.json", "capture.json"))
    cases.write_text(json.dumps(suite(1)))
    oracle.write_text(json.dumps(ORACLE))
    monkeypatch.setattr(ProviderSettings, "from_env", lambda: SETTINGS)
    monkeypatch.setenv("DATABASE_URL", "unused-by-fixture")
    monkeypatch.setattr(runner, "fingerprints", lambda *args: {"source_sha256": "fixture-hash"})
    monkeypatch.setattr(runner, "CaptureGateway", lambda _: SimpleNamespace(request_ids=set()))
    monkeypatch.setattr(runner, "PinnedFacts", lambda *args: SimpleNamespace(
        assert_publication=lambda: None))
    monkeypatch.setattr(runner, "create_app", lambda *args, **kwargs: app_for([answer()], seen))
    accounted: list[set[str]] = []

    def ledger(_: Any, request_ids: set[str]) -> dict[str, Any]:
        accounted.append(request_ids)
        return {"status": "captured", "settled_nusd": 100, "held_nusd": 200, "operations": []}

    monkeypatch.setattr(runner, "accounting", ledger)
    code = main(["--live", "--cases", str(cases), "--oracle", str(oracle), "--out", str(out),
                 "--max-total-usd", "0.025"])
    assert code == 0  # Successful capture is explicitly separate from answer quality.
    report = json.loads(out.read_text())
    assert report["outcome_review"] == "unreviewed"
    assert report["summary"]["quality_conclusion"] is None
    assert report["accounting"]["held_nusd"] == 200
    assert accounted == [{"fixture-request"}]
    assert report["turns"][0]["body"]["answer"] == "actual fixture answer"
    assert report["inputs_unchanged"] is True
    assert "fake-secret" not in out.read_text()



def test_budget_preflight_refuses_before_constructing_live_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import capture_chat_live as runner

    cases, oracle, out = (tmp_path / name for name in ("cases.json", "oracle.json", "capture.json"))
    cases.write_text(json.dumps(suite(2)))
    oracle.write_text(json.dumps(ORACLE))
    monkeypatch.setattr(ProviderSettings, "from_env", lambda: SETTINGS)

    def never(_: Any) -> Any:
        pytest.fail("Over-budget execution must not construct a provider")

    monkeypatch.setattr(runner, "CaptureGateway", never)
    with pytest.raises(SystemExit) as caught:
        main(["--live", "--cases", str(cases), "--oracle", str(oracle), "--out", str(out),
              "--max-total-usd", "0.025"])
    assert caught.value.code == 2
    assert not out.exists()


def test_ledger_capture_is_read_only_and_separates_settled_cost_from_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import capture_chat_live as runner

    statements: list[tuple[str, Any]] = []

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def execute(self, query: str, params: Any = None) -> Any:
            statements.append((query, params))
            return SimpleNamespace(fetchall=lambda: [
                {"state": "settled", "cost_nusd": 123, "reserved_nusd": 1000},
                {"state": "uncertain", "cost_nusd": None, "reserved_nusd": 2000},
                {"state": "reserved", "cost_nusd": None, "reserved_nusd": 3000},
            ])

    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: Connection())
    result = runner.accounting(SETTINGS, {"request-two", "request-one"})
    assert result["settled_nusd"] == 123
    assert result["held_nusd"] == 5000
    assert result["committed_nusd"] == 5123
    assert statements[0][0] == "SET TRANSACTION READ ONLY"
    assert statements[-1][1] == (["request-one", "request-two"],)
    assert all(query.startswith(("SET", "SELECT")) for query, _ in statements)


def test_ledger_capture_error_never_records_connection_or_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import capture_chat_live as runner

    def failed(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret connection URL")

    monkeypatch.setattr(psycopg, "connect", failed)
    result = runner.accounting(SETTINGS, {"request"})
    assert result["status"] == "unavailable"
    assert result["error_type"] == "RuntimeError"
    assert result["held_nusd"] is None
    assert "secret" not in json.dumps(result)



def test_non_object_execution_protocol_has_a_safe_configuration_error() -> None:
    cases = suite(1)
    cases["method_metadata"]["execution_protocol"] = "invalid fixture protocol"
    with pytest.raises(CaptureError, match="execution protocol"):
        preflight(cases, ORACLE, SETTINGS, 25_000_000)
