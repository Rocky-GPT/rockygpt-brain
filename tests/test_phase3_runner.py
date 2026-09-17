"""The paid runner preserves actual follow-ups and stops after a quota rejection."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import evaluate_phase3 as runner
import httpx
import pytest

from rockygpt_brain.accounting import PaidCallError
from rockygpt_brain.contracts import ChatMessage


@pytest.mark.parametrize("quota_failure, limited", [(False, False), (True, False), (False, True)])
def test_runner_uses_real_history_and_stops_globally_on_quota(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, quota_failure: bool, limited: bool
) -> None:
    cases = tmp_path / "cases.json"
    report = tmp_path / "report.json"
    cases.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "followup",
                        "messages": [],
                        "turns": [{"user": "First question"}, {"user": "And tomorrow?"}],
                    },
                    {"id": "later", "messages": [{"role": "user", "content": "Another question"}]},
                ]
            }
        )
    )
    monkeypatch.setattr(
        "sys.argv",
        ["evaluate_phase3", "--cases", str(cases), "--output", str(report)]
        + (["--max-run-cost-usd", "0.8"] if limited else []),
    )
    monkeypatch.setattr(runner, "load_dotenv", Mock())
    monkeypatch.setattr(
        runner,
        "load_deployment",
        lambda: SimpleNamespace(environment="development", ledger_url="test"),
    )
    monkeypatch.setattr(runner, "configuration_hash", lambda: "fixed-test-source")
    monkeypatch.setenv("DATABASE_URL", "test")
    ledger = MagicMock()
    ledger.account.return_value = {"paused": False, "cap_nusd": 10_000_000_000}
    ledger.monthly_cap.return_value = 30_000_000_000
    connection = ledger.transaction.return_value.__enter__.return_value
    connection.execute.return_value.fetchone.return_value = {"committed": 0}
    monkeypatch.setattr(runner, "PostgresLedger", Mock(return_value=ledger))
    data = Mock(outputs=[])
    monkeypatch.setattr(runner, "CapturedData", Mock(return_value=data))
    gateway = Mock()
    gateway.usage.report.return_value = {"modelCalls": 1, "costNusd": 1, "unsettledNusd": 0}
    gateway_context = MagicMock()
    gateway_context.__enter__.return_value = gateway
    monkeypatch.setattr(runner, "open_gateway", Mock(return_value=gateway_context))
    requests = []

    def execute(messages: list[Any], **kwargs: Any) -> dict[str, Any]:
        requests.append([message.model_dump() for message in messages])
        if quota_failure:
            raise PaidCallError("model_quota_exhausted")
        return {"status": "answered", "answer": f"Actual answer {len(requests)}", "metrics": {}}

    monkeypatch.setattr(runner, "run_turn", execute)
    runner.main()
    result = json.loads(report.read_text())
    assert result["requested"] == 3 and result["requestedConversations"] == 2
    if limited:
        assert len(requests) == 1
        assert result["completed"] == 1
        assert result["stoppedReason"] == "run_allowance_exhausted"
        assert len(result["notRun"]) == 2
        assert result["preflight"]["runCeilingNusd"] == 800_000_000
        assert result["preflight"]["requestedWorstCaseNusd"] == 2_400_000_000
    elif quota_failure:
        assert len(requests) == 1
        assert result["stoppedReason"] == "model_quota_exhausted"
        assert result["completed"] == 0
    else:
        assert result["completed"] == 3
        assert requests[1] == [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "Actual answer 1"},
            {"role": "user", "content": "And tomorrow?"},
        ]
        assert result["runs"][0]["messages"] == requests[0]
        assert result["runs"][1]["messages"] == requests[1]
        assert requests[2] == [{"role": "user", "content": "Another question"}]


@pytest.mark.parametrize("failure", [None, "configuration", "evidence"])
def test_http_run_retains_uncertain_cost_and_checks_source_and_evidence(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    from rockygpt_brain.contracts import ChatMessage

    payload = {
        "requestId": "http-turn",
        "status": "answered",
        "answer": "A supported answer",
        "datasetVersion": "frozen-one",
        "trace": [
            {
                "status": "ok",
                "tool": "search_campus",
                "arguments": {"collection": "contacts", "query": "Library"},
                "evidence_ids": ["office"],
            }
        ],
    }
    http_client = MagicMock()
    reply = http_client.return_value.__enter__.return_value.post.return_value
    reply.status_code = 200
    reply.json.return_value = payload
    monkeypatch.setattr(httpx, "Client", http_client)
    monkeypatch.setenv("STAGING_SERVICE_TOKEN", "synthetic-token")
    operation = {
        "operation_id": "op",
        "category": "draft",
        "state": "settled",
        "reserved_nusd": 100,
        "cost_nusd": 3,
        "usage": {},
        "elapsed_ms": 9,
        "returned_model": "fixture",
        "metadata": {"configuration_hash": "wrong" if failure == "configuration" else "expected"},
    }
    ledger = Mock()
    ledger.operations.return_value = [
        operation,
        {**operation, "operation_id": "pending", "state": "uncertain", "cost_nusd": None},
    ]
    data = Mock(outputs=[])
    data.search.return_value = {
        "dataset_version": "frozen-one",
        "records": [{"id": "different" if failure == "evidence" else "office"}],
    }
    result = runner.execute_http(
        "http://127.0.0.1:8000",
        [ChatMessage(role="user", content="A question")],
        data,
        ledger,
        "expected",
    )
    ledger.operations.assert_called_once_with("http-turn")
    assert result["usage"] == {
        "modelCalls": 2,
        "costNusd": 3,
        "unsettledNusd": 100,
        "usageComplete": False,
    }
    if failure:
        assert "result" not in result
        assert result["error"] == (
            "server_configuration_mismatch"
            if failure == "configuration"
            else "evidence_replay_mismatch"
        )
    else:
        assert result["result"] == payload
        assert result["httpStatus"] == 200
        assert result["evidenceOrigin"] == "replayed_frozen_trace_with_verified_ids"


@pytest.mark.parametrize("status", [200, 504, None])
def test_http_stream_keeps_preview_separate_from_final_result(
    monkeypatch: pytest.MonkeyPatch, status: int | None
) -> None:
    payload = {"requestId": "stream-turn", "answer": "Final answer", "trace": []}
    if status == 504:
        payload = {"requestId": "stream-turn", "reason": "model_timeout"}
    frames = 'event: progress\ndata: {"stage":"reviewing","draft":"Unchecked text"}\n\n'
    if status is not None:
        frames += "event: result\ndata: " + json.dumps({"status": status, "body": payload}) + "\n\n"

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "text/event-stream"
        assert request.url.path == "/v1/chat"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=frames)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    ledger = Mock()
    ledger.operations.return_value = []
    args = (
        "http://127.0.0.1:8000",
        [ChatMessage(role="user", content="Question")],
        Mock(outputs=[]),
        ledger,
        "expected",
    )
    if status is None:
        with pytest.raises(ValueError, match="without a final result"):
            runner.execute_http(*args, stream=True)
        ledger.operations.assert_not_called()
        return
    row = runner.execute_http(*args, stream=True)
    assert row["httpStatus"] == status
    assert row["progress"][0]["draft"] == "Unchecked text"
    assert row["progress"][0]["elapsedMs"] >= 0
    if status == 504:
        assert row["error"] == "model_timeout" and "result" not in row
    else:
        assert row["result"]["answer"] == "Final answer"
