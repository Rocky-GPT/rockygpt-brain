"""Check the evaluator does not mistake its own assumptions for service behavior."""

from typing import Any

from check_chat import digest, run, score, summarize
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


def test_replay_uses_actual_answer_and_preserves_omitted_history() -> None:
    seen: list[dict[str, Any]] = []
    app = FastAPI()

    @app.post("/v1/chat")
    def chat(request: dict[str, Any]) -> JSONResponse:
        seen.append(request)
        return JSONResponse({"requestId": "test", "status": "answered", "citations": [],
                             "answer": f"actual answer {len(seen)}"},
                            headers={"X-Request-Id": "test"})

    suite = {"conversations": [{"id": "follow-up", "turns": [
        {"user": "Where is the office?", "expected": {"http_status": 200}},
        {"user": "And the phone?", "omitted_messages": 14,
         "expected": {"http_status": 200}},
    ]}]}
    results = run(suite, lambda _: TestClient(app))
    assert seen[1] == {"messages": [
        {"role": "user", "content": "Where is the office?"},
        {"role": "assistant", "content": "actual answer 1"},
        {"role": "user", "content": "And the phone?"},
    ], "omittedMessages": 14}
    assert summarize(results)["all_regressions_pass"] is True


def test_scorer_separates_missing_content_refusal_and_fabricated_evidence() -> None:
    failures = score(200, {
        "requestId": "test", "status": "partial",
        "answer": "Your GPA is 4.0. [Office](https://example.edu/office)",
        "citations": [{"id": "made-up", "title": "Wrong record",
                       "url": "https://example.edu/other"}], "datasetVersion": "fixture-1",
    }, {
        "http_status": 200, "status": "partial", "contains": ["555-0100"],
        "excludes": ["Your GPA is 4.0"], "refusal_contains": ["can't access"],
        "citation_ids": ["office-phone"], "allowed_citation_ids": ["office-phone"],
    }, "test")
    assert not failures["contract"]
    assert len(failures["support"]) == 4
    assert len(failures["completion"]) == 1
    assert len(failures["refusal"]) == 1
    assert not failures["error"]


def test_scorer_requires_failure_code_retry_behavior_and_request_id() -> None:
    expected = {"http_status": 503, "error_code": "data_unavailable", "retryable": True}
    body = {"requestId": "one", "error": {"code": "data_unavailable", "message": "Unavailable",
                                          "retryable": False}}
    failures = score(503, body, expected, "different")
    assert failures["contract"] == ["missing or mismatched request ID"]
    assert failures["error"] == ["wrong retry behavior"]


def test_empty_run_does_not_pass_and_json_fingerprints_are_stable() -> None:
    assert summarize([])["all_regressions_pass"] is False
    assert digest({"one": 1, "two": 2}) == digest({"two": 2, "one": 1})
    assert digest({"one": 1}) != digest({"one": 2})


def test_scorer_does_not_accept_empty_success_envelope() -> None:
    failures = score(200, {"requestId": "one"}, {"http_status": 200}, "one")
    assert len(failures["contract"]) == 3
