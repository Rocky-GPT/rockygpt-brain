import json

from profile_turns import region_of, spread, summarize_responses, summarize_turns


def test_region_names_the_cloud_region_never_the_host() -> None:
    assert region_of("ep-quiet-sky-123456.us-east-2.aws.neon.tech") == {
        "provider": "neon", "region": "aws-us-east-2", "pooled": False}
    assert region_of("ep-quiet-sky-123456-pooler.c-2.us-west-2.aws.neon.tech") == {
        "provider": "neon", "region": "aws-us-west-2", "pooled": True}
    assert region_of("db.example.com") == {"provider": "other"}
    assert region_of("127.0.0.1") == {"provider": "local"}
    assert region_of(None) == {"provider": "unknown"}


def test_spread() -> None:
    assert spread([]) == {"n": 0}
    assert spread([1.0, 2.0, 3.0, 10.0]) == {
        "n": 4, "median": 2.5, "p90": 10.0, "max": 10.0, "mean": 4.0}


def test_responses_split_client_brain_and_model_time() -> None:
    body = {"elapsedMs": 9000, "metrics": {"draftModelMs": 5000, "reviewModelMs": 2500,
                                           "retrievalMs": 500, "modelCalls": 3}}
    result = summarize_responses([{"seconds": 10.0, "raw": json.dumps(body)},
                                  {"seconds": 1.0, "raw": "not json"}])
    assert result["responses"] == 1
    row = result["rows"][0]
    assert row["otherBrainMs"] == 1000
    assert row["outsideBrainMs"] == 1000


def test_turn_summary_counts_stages_and_calls() -> None:
    turns = [
        {"elapsedMs": 100.0, "cpuMs": 10.0, "status": "answered", "responseMode": "reviewed_prose",
         "stages": {"ledger.reserve": {"ms": 30.0, "count": 3}},
         "calls": [{"category": "draft", "elapsedMs": 50, "input_tokens": 100,
                    "cached_input_tokens": 80, "output_tokens": 10, "reasoning_tokens": 5,
                    "costNusd": 7}]},
        {"elapsedMs": 300.0, "cpuMs": 30.0, "error": "model_timeout"},
    ]
    summary = summarize_turns(turns)
    assert summary["turns"] == 2 and summary["errors"] == 1
    assert summary["stages"]["ledger.reserve"]["callsPerTurn"] == 3
    assert summary["modelCalls"]["draft"]["cachedShare"] == 0.8
    assert summary["costNusdPerTurn"]["median"] == 7
