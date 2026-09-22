"""Jev may select retrieval, but never bypass evidence, accounting or deadlines."""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any
from unittest.mock import Mock
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import pytest

from rockygpt_brain.config import RELEASE, Deployment
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.provider import JevProvider, ModelResponse, PaidGateway, Usage
from rockygpt_brain.core.routing import (
    interpret,
    route_request,
    routing_payload,
    shortlist,
    validate_answers,
)
from rockygpt_brain.governance.accounting import PaidCallError
from rockygpt_brain.retrieval.profiles import Identity
from test_engine import answer, review, search, tools
from test_phase2 import result_for
from test_provider import arguments

NOW = datetime(2026, 9, 22, 12, tzinfo=ZoneInfo("America/New_York"))
ENTITY = Identity.model_validate(
    {
        "id": "00000000-0000-0000-0000-000000000001",
        "kind": "office",
        "name": "Registrar",
        "aliases": ["Registration Office"],
        "links": [
            {
                "collection": "contacts",
                "source_key": "directory",
                "source_record_keys": ["registrar"],
            }
        ],
    }
)


def messages(text: str = "What is the Registrar phone?") -> list[ChatMessage]:
    return [ChatMessage(role="user", content=text)]


def answers_for(payload: dict[str, Any], **selections: Any) -> dict[str, Any]:
    choices = {
        "route": "contact",
        "entity": str(ENTITY.id),
        "date": "unspecified",
        "meal": "unspecified",
        "simple": 1.0,
        "field_phone": 1.0,
        "section_contact": 1.0,
        **selections,
    }
    answers: dict[str, Any] = {}
    for key, question in payload["questions"].items():
        if question["type"] == "choice":
            selected = choices.get(key, "unresolved")
            answers[key] = {
                "type": "choice",
                "choice": selected,
                "confidence": 1.0,
                "probabilities": {
                    option: float(option == selected) for option in question["criteria"]
                },
            }
        else:
            answers[key] = {"type": "noul", "noul": choices.get(key, 0.0)}
    return answers


def data_mock() -> Mock:
    data = Mock()
    data.deadline = None
    data._artifact.return_value = {
        "schema_version": 1,
        "entities": [ENTITY.model_dump(mode="json")],
    }
    return data


def router_mock(**choices: Any) -> Mock:
    router = Mock()
    router.route.side_effect = lambda payload, **kwargs: answers_for(payload, **choices)
    return router


def contact_record() -> dict[str, Any]:
    record: dict[str, Any] = json.loads(
        (Path(__file__).parents[1] / "docs/phase2/published-contact.json").read_text()
    )["contact_search"]["records"][0]
    record.update(
        entity_id="campus-directory:office:registrar",
        freshness="fresh",
        collected_at=NOW.isoformat(),
        valid_from=None,
        valid_until=None,
    )
    record["coverage"] = {"fields": {key: "published" for key in record["fields"]}}
    return record


def test_direct_contact_uses_only_jev_and_preserves_citations() -> None:
    data, router, gpt = data_mock(), router_mock(), Mock()
    record = contact_record()
    data.lookup_contact.return_value = result_for([record])
    progress = Mock()
    result = run_turn(
        messages(),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router,
        routing_mode="active",
        progress=progress,
    )
    assert result["status"] == "answered"
    assert "201-684-7695" in result["answer"]
    assert result["model"] == RELEASE.routing.model
    assert result["metrics"]["modelCalls"] == result["metrics"]["routingCalls"] == 1
    assert result["metrics"]["draftCalls"] == result["metrics"]["reviewCalls"] == 0
    assert result["metrics"]["routing"]["directRetrieval"] is True
    assert result["citations"][0]["url"] == record["url"]
    gpt.create.assert_not_called()
    router.route.assert_called_once()
    assert "retrieving" in [call.args[0]["stage"] for call in progress.call_args_list]


def test_direct_profile_preserves_review_and_allows_more_retrieval() -> None:
    data = data_mock()
    record = contact_record()
    data.lookup_profile.return_value = result_for([record])
    data.search.return_value = result_for([record])
    gpt = Mock()
    gpt.create.side_effect = [
        tools(search()),
        answer("Office D-224", "campus_fact", [record["id"]]),
        review(),
    ]
    result = run_turn(
        messages("Tell me about the Registrar"),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(route="profile"),
        routing_mode="active",
    )
    assert result["metrics"]["routing"]["directRetrieval"]
    assert result["metrics"]["reviewCalls"] == 1
    assert result["metrics"]["modelCalls"] == 4
    assert result["model"] == "test-model"
    first = gpt.create.call_args_list[0].kwargs
    assert first["tool_choice"] == "auto" and len(first["tools"]) == 5
    assert first["input"][0] == messages("Tell me about the Registrar")[0].model_dump()
    assert first["input"][1].name == "lookup_profile"
    assert first["input"][2]["call_id"] == first["input"][1].call_id
    assert record["url"] in first["input"][2]["output"]


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_do_not_change_tool_selection(mode: Any) -> None:
    data, router, gpt = data_mock(), router_mock(), Mock()
    gpt.create.side_effect = [answer(), review()]
    result = run_turn(
        messages(),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router,
        routing_mode=mode,
    )
    data.lookup_contact.assert_not_called()
    assert gpt.create.call_args_list[0].kwargs["tool_choice"] == "auto"
    assert router.route.call_count == (mode == "shadow")
    assert result["metrics"]["modelCalls"] == 2 + (mode == "shadow")


def test_confident_route_without_arguments_constrains_only_first_call() -> None:
    data, gpt = data_mock(), Mock()
    record = contact_record()
    data.search.return_value = result_for([record])
    gpt.create.side_effect = [
        tools(search()),
        answer("Office D-224", "campus_fact", [record["id"]]),
        review(),
    ]
    run_turn(
        messages(),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(route="search"),
        routing_mode="active",
    )
    first, second = [call.kwargs for call in gpt.create.call_args_list[:2]]
    assert first["tool_choice"] == {"type": "function", "name": "search_campus"}
    assert [tool["name"] for tool in first["tools"]] == ["search_campus"]
    assert second["tool_choice"] == "auto" and len(second["tools"]) == 5


@pytest.mark.parametrize(
    "mutation",
    [
        {"freshness": "stale"},
        {"trust_tier": "community"},
        {"content_truncated": True},
        {"coverage": {}},
        {"valid_until": "2000-01-01"},
    ],
)
def test_direct_contact_does_not_relax_exact_validation(mutation: dict[str, Any]) -> None:
    data, gpt = data_mock(), Mock()
    record = contact_record()
    record.update(mutation)
    data.lookup_contact.return_value = result_for([record])
    result = run_turn(
        messages(),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(),
        routing_mode="active",
    )
    assert result["status"] == "unavailable"
    assert "201-684-7695" not in result["answer"]
    gpt.create.assert_not_called()


def test_rejected_prose_after_direct_lookup_is_not_returned() -> None:
    data, gpt = data_mock(), Mock()
    record = contact_record()
    data.lookup_profile.return_value = result_for([record])
    gpt.create.side_effect = [
        answer("invented claim", "campus_fact", [record["id"]]),
        review("unsupported_claim"),
    ]
    result = run_turn(
        messages("Describe the Registrar"),
        client=gpt,
        data=data,
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(route="profile"),
        routing_mode="active",
    )
    assert result["status"] == "unavailable" and "invented claim" not in result["answer"]


@pytest.mark.parametrize("score,confident", [(0.8999, False), (0.90, True), (1.0, True)])
def test_choice_requires_both_probability_and_confidence(score: float, confident: bool) -> None:
    payload, day = routing_payload(messages(), [ENTITY], NOW)
    for field in ("probability", "confidence"):
        answers = answers_for(payload)
        if field == "confidence":
            answers["route"]["confidence"] = score
        else:
            answers["route"]["probabilities"]["contact"] = score
            answers["route"]["probabilities"]["unresolved"] = 1 - score
        validate_answers(answers, payload["questions"])
        assert (interpret(answers, [ENTITY], day, messages()).arguments is not None) == confident


@pytest.mark.parametrize(
    "value,direct", [(0.1, True), (0.1001, False), (0.8999, False), (0.9, True)]
)
def test_uncertain_field_selection_defers(value: float, direct: bool) -> None:
    payload, day = routing_payload(messages(), [ENTITY], NOW)
    result = interpret(answers_for(payload, field_email=value), [ENTITY], day, messages())
    assert (result.arguments is not None) == direct


@pytest.mark.parametrize(
    "changes", [{"simple": 0.89}, {"entity": "unresolved"}, {"route": "unresolved"}]
)
def test_ambiguity_and_mixed_requests_defer(changes: dict[str, Any]) -> None:
    payload, day = routing_payload(messages(), [ENTITY], NOW)
    result = interpret(answers_for(payload, **changes), [ENTITY], day, messages())
    assert result.arguments is None
    if "simple" in changes or "route" in changes:
        assert result.tool is None


def test_duplicate_aliases_never_select_an_arbitrary_identity() -> None:
    other = ENTITY.model_copy(
        update={"id": UUID(int=2), "name": "Other Office", "aliases": ["Registrar"]}
    )
    payload, day = routing_payload(messages(), [ENTITY, other], NOW)
    result = interpret(answers_for(payload), [ENTITY, other], day, messages())
    assert result.arguments is None and result.tool is None


def test_shortlist_prioritizes_latest_exact_match_and_caps_candidates() -> None:
    entities = [
        ENTITY.model_copy(update={"id": UUID(int=i + 2), "name": f"Registrar {i}", "aliases": []})
        for i in range(40)
    ]
    ranked = shortlist([*entities, ENTITY], messages())
    assert ranked[0] == ENTITY and len(ranked) == 24
    followup = [
        *messages(),
        ChatMessage(role="assistant", content="Registrar has a directory."),
        ChatMessage(role="user", content="What is their email?"),
    ]
    assert ENTITY in shortlist([ENTITY], followup)
    payload, _ = routing_payload(followup, [ENTITY], NOW)
    assert payload["state"]["latest_request"] == "What is their email?"
    assert payload["state"]["prior_messages"] == [message.model_dump() for message in followup[:-1]]


def test_dates_meals_all_sections_and_complete_menu_are_bounded() -> None:
    request = messages("Registrar menu and hours tomorrow for dinner")
    payload, day = routing_payload(request, [ENTITY], NOW)
    assert day == (NOW.date() + timedelta(days=1)).isoformat()
    answers = answers_for(
        payload,
        route="profile",
        date="explicit",
        meal="dinner",
        section_menu=1.0,
        section_hours=1.0,
        complete_menu=1.0,
    )
    result = interpret(answers, [ENTITY], day, request)
    assert result.arguments is not None
    assert result.arguments["date"] == day and result.arguments["meal"] == "dinner"
    assert result.arguments["menu_limit"] == 100
    assert set(result.arguments["include"]) == {"contact", "menu", "hours"}
    answers["date"]["choice"] = "unresolved"
    assert interpret(answers, [ENTITY], day, request).arguments is None


@pytest.mark.parametrize("mutation", ["missing", "nan", "unknown", "sum", "bool", "wrong_type"])
def test_invalid_provider_answers_fall_back(mutation: str) -> None:
    def malformed(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        answers = answers_for(payload)
        if mutation == "missing":
            del answers["route"]
        elif mutation == "nan":
            answers["simple"]["noul"] = float("nan")
        elif mutation == "unknown":
            answers["route"]["choice"] = "invented_tool"
        elif mutation == "sum":
            answers["route"]["probabilities"]["contact"] = 0.4
        elif mutation == "bool":
            answers["simple"]["noul"] = True
        else:
            answers["route"]["type"] = "noul"
        return answers

    router = Mock()
    router.route.side_effect = malformed
    result = route_request(messages(), data=data_mock(), client=router, now=NOW, timeout=2)
    assert result.reason == "routing_invalid_response" and result.arguments is None


@pytest.mark.parametrize(
    "code",
    [
        "routing_provider_error",
        "routing_usage_unknown",
        "routing_model_changed",
        "routing_price_unavailable",
    ],
)
def test_optional_provider_failures_fall_back(code: str) -> None:
    router = Mock()
    router.route.side_effect = PaidCallError(code)
    result = route_request(messages(), data=data_mock(), client=router, now=NOW, timeout=2)
    assert result.reason == code and result.arguments is None


@pytest.mark.parametrize(
    "code",
    ["accounting_unavailable", "budget_exhausted", "turn_cost_limit", "accounting_bound_exceeded"],
)
def test_accounting_failures_never_become_fallback(code: str) -> None:
    router = Mock()
    router.route.side_effect = PaidCallError(code)
    with pytest.raises(PaidCallError, match=code):
        route_request(messages(), data=data_mock(), client=router, now=NOW, timeout=2)


def test_context_overflow_does_not_send_truncated_history() -> None:
    router = router_mock()
    request = messages("é" * 15000)
    result = route_request(request, data=data_mock(), client=router, now=NOW, timeout=2)
    assert result.reason == "routing_context_limit" and result.calls == 0
    router.route.assert_not_called()


def test_elapsed_routing_time_is_inside_existing_turn_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rockygpt_brain.core import engine
    from rockygpt_brain.core.routing import RouteDecision

    clock = [100.0]
    monkeypatch.setattr(engine, "monotonic", lambda: clock[0])

    def routed(*args: Any, **kwargs: Any) -> RouteDecision:
        clock[0] += 2
        return RouteDecision(reason="routing_timeout", calls=1)

    monkeypatch.setattr(engine, "route_request", routed)
    gpt = Mock()
    gpt.create.side_effect = [answer(), review()]
    run_turn(
        messages(),
        client=gpt,
        data=data_mock(),
        model=RELEASE.model,
        now=NOW,
        routing_client=router_mock(),
        routing_mode="active",
    )
    timeout = gpt.create.call_args_list[0].kwargs["timeout"].read
    assert timeout == RELEASE.turn_seconds - RELEASE.review_reserve_seconds - 2


def gateway_setup() -> tuple[PaidGateway, Mock, Mock, Mock]:
    provider, ledger, jev = Mock(), Mock(), Mock()
    payload, _ = routing_payload(messages(), [ENTITY], NOW)
    jev.create.return_value = ModelResponse(
        "",
        RELEASE.routing.model,
        "completed",
        json.dumps(answers_for(payload)),
        [],
        Usage(100, 0, 200, 0),
    )
    gateway = PaidGateway(provider, ledger, "routing-test", routing_provider=jev, clock=lambda: NOW)
    return gateway, provider, ledger, jev


def test_routing_charges_input_only_and_preserves_gpt_capacity() -> None:
    gateway, _, ledger, jev = gateway_setup()
    payload, _ = routing_payload(messages(), [ENTITY], NOW)
    gateway.route(payload, timeout=2)
    assert ledger.reserve.call_args.args[2] == "routing"
    metadata = ledger.reserve.call_args.args[4]
    assert metadata["provider"] == "typesafe"
    assert metadata["price"]["output_nusd"] == 0
    assert "Registrar" not in json.dumps(metadata)
    assert ledger.settle.call_args.args[1] == 4200
    assert ledger.settle.call_args.args[3].startswith("local-operation:")
    assert gateway.usage.report()["routingCalls"] == 1
    assert gateway.usage.report()["modelCalls"] == 1
    assert gateway.budget.draft_calls == 0
    assert gateway.budget.can_retrieve
    with pytest.raises(PaidCallError, match="model_call_limit"):
        gateway.route(payload, timeout=2)
    jev.create.assert_called_once()


def test_uncertain_jev_charge_is_retained_before_gpt_fallback() -> None:
    gateway, provider, ledger, jev = gateway_setup()
    jev.create.side_effect = TimeoutError()
    payload, _ = routing_payload(messages(), [ENTITY], NOW)
    with pytest.raises(PaidCallError, match="routing_provider_error"):
        gateway.route(payload, timeout=2)
    ledger.uncertain.assert_called_once()
    assert gateway.usage.report()["unsettledNusd"] > 0
    provider.create.return_value = ModelResponse(
        "gpt-id", RELEASE.model, "completed", "", [], Usage(10, 0, 10, 0)
    )
    gateway.create(category="draft", **arguments())
    assert gateway.usage.report()["modelCalls"] == 2
    assert not gateway.usage.report()["usageComplete"]


def test_jev_settlement_failure_stops_further_work() -> None:
    gateway, provider, ledger, _ = gateway_setup()
    ledger.settle.side_effect = PaidCallError("accounting_unavailable")
    with pytest.raises(PaidCallError, match="accounting_unavailable"):
        gateway.route(routing_payload(messages(), [ENTITY], NOW)[0], timeout=2)
    provider.create.assert_not_called()


def test_jev_missing_usage_keeps_reservation() -> None:
    gateway, _, ledger, jev = gateway_setup()
    jev.create.return_value.usage = None
    with pytest.raises(PaidCallError, match="routing_usage_unknown"):
        gateway.route(routing_payload(messages(), [ENTITY], NOW)[0], timeout=2)
    ledger.settle.assert_not_called()
    ledger.uncertain.assert_called_once()


def test_jev_total_deadline_cancels_slow_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self) -> Any:
            try:
                yield b"{"
                await asyncio.sleep(1)
                yield b"}"
            finally:
                closed.append(True)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=SlowBody()))
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
    )
    started = monotonic()
    with pytest.raises(TimeoutError):
        JevProvider("secret").create(
            timeout=0.03, model=RELEASE.routing.model, state="hello", questions={}
        )
    assert monotonic() - started < 0.5 and closed


def test_enabled_routing_requires_credential_and_secrets_stay_out_of_repr() -> None:
    values = dict(
        environment="development", api_key="gpt-secret", project="project", ledger_url="db"
    )
    assert Deployment.model_validate(values).routing_mode == "off"
    with pytest.raises(ValueError):
        Deployment.model_validate({**values, "routing_mode": "active"})
    deployment = Deployment.model_validate(
        {**values, "routing_mode": "active", "typesafe_api_key": "jev-secret"}
    )
    assert "jev-secret" not in repr(deployment)


@pytest.mark.parametrize("status", [401, 429, 529])
def test_jev_http_failures_are_single_attempts(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={"error": "upstream"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(httpx.HTTPStatusError):
        JevProvider("secret").create(
            timeout=1, model=RELEASE.routing.model, state="hello", questions={}
        )
    assert len(requests) == 1
    assert requests[0].url == "https://api.typesafe.ai/v1/systemone"
    assert requests[0].headers["authorization"] == "Bearer secret"


def test_jev_model_drift_is_billed_but_not_used() -> None:
    gateway, _, ledger, jev = gateway_setup()
    jev.create.return_value.model = "jev-unreviewed"
    with pytest.raises(PaidCallError, match="routing_model_changed"):
        gateway.route(routing_payload(messages(), [ENTITY], NOW)[0], timeout=2)
    ledger.settle.assert_called_once()
    assert gateway.usage.report()["usageComplete"]


def test_database_failure_and_expired_routing_deadline_do_not_call_jev() -> None:
    data, router = data_mock(), router_mock()
    data._ensure_loaded.side_effect = RuntimeError("private connection details")
    result = route_request(messages(), data=data, client=router, now=NOW, timeout=2)
    assert result.reason == "routing_data_unavailable"
    assert "private" not in json.dumps(result.metrics("active"))
    data._ensure_loaded.side_effect = None
    result = route_request(messages(), data=data, client=router, now=NOW, timeout=0)
    assert result.reason == "routing_timeout"
    router.route.assert_not_called()
