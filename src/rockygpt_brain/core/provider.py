"""The sole SDK boundary and paid-call gateway. No retries or unaccounted calls."""

import asyncio
import json
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

import httpx
from httpx import Timeout
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from rockygpt_brain.config import (
    RELEASE,
    Deployment,
    Price,
    Release,
    RoutingProvider,
    configuration_hash,
)
from rockygpt_brain.governance.accounting import (
    CAMPUS_ZONE,
    Category,
    Ledger,
    PaidCallError,
    PostgresLedger,
)
from rockygpt_brain.governance.budget import TurnBudget


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in asdict(self).values()):
            raise ValueError("Invalid token usage")
        if (
            self.cached_input_tokens > self.input_tokens
            or self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError("Invalid token breakdown")

    def cost(self, price: Price) -> int:
        # Cached tokens are a subset of input; reasoning is already included in output.
        return (
            (self.input_tokens - self.cached_input_tokens) * price.input_nusd
            + self.cached_input_tokens * price.cached_input_nusd
            + self.output_tokens * price.output_nusd
        )


@dataclass
class OutputItem:
    payload: dict[str, Any]

    @property
    def type(self) -> str:
        return str(self.payload["type"])

    @property
    def name(self) -> str:
        return str(self.payload["name"])

    @property
    def call_id(self) -> str:
        return str(self.payload["call_id"])

    @property
    def arguments(self) -> str:
        return str(self.payload["arguments"])

    def model_dump(self) -> dict[str, Any]:
        return self.payload


@dataclass
class ModelResponse:
    id: str
    model: str
    status: str
    output_text: str
    output: list[OutputItem]
    usage: Usage | None


class Provider(Protocol):
    def create(self, **kwargs: Any) -> ModelResponse: ...


class ModelClient(Protocol):
    def create(self, *, category: Category, **kwargs: Any) -> ModelResponse: ...


def normalize_usage(raw: Any) -> Usage | None:
    try:
        return Usage(
            raw.input_tokens,
            raw.input_tokens_details.cached_tokens,
            raw.output_tokens,
            raw.output_tokens_details.reasoning_tokens,
        )
    except (AttributeError, ValueError, TypeError):
        return None


class OpenAIProvider:
    def __init__(self, client: OpenAI) -> None:
        self._client = client

    def create(self, **kwargs: Any) -> ModelResponse:
        response = self._client.responses.create(**kwargs)
        output = [
            OutputItem(item.model_dump(mode="json", exclude_none=True)) for item in response.output
        ]
        messages = [item.payload for item in output if item.type == "message"]
        final = [item for item in messages if item.get("phase") == "final_answer"]
        # output_text concatenates commentary and final messages. Select the
        # completed final candidate; identical duplicate messages are one value.
        # Older responses without phase are accepted only when unambiguous.
        if not final and len(messages) == 1 and messages[0].get("phase") is None:
            final = messages
        text = ""
        if final and all(item.get("status") == "completed" for item in final):
            candidates = {
                "".join(
                    block["text"]
                    for block in item.get("content", [])
                    if block.get("type") == "output_text"
                )
                for item in final
            }
            if len(candidates) == 1:
                text = candidates.pop()
        return ModelResponse(
            id=response.id,
            model=response.model,
            status=response.status or "unknown",
            output_text=text,
            output=output,
            usage=normalize_usage(response.usage),
        )


JEV_URLS: dict[RoutingProvider, str] = {
    "typesafe": "https://api.typesafe.ai/v1/systemone",
    # OpenRouter forwards the same System One request and answers to TypeSafe.
    "openrouter": "https://openrouter.ai/api/v1/systemone",
}
# OpenRouter renames models: pinned model -> (requested ID, reported snapshot).
OPENROUTER_MODELS = {"jev-1.13.0": ("typesafe/jev-1.13", "typesafe/jev-1.13-20260917")}


class JevProvider:
    """One cancellable HTTP attempt; the deadline includes reading the response body."""

    def __init__(self, api_key: str, name: RoutingProvider = "typesafe") -> None:
        self._api_key = api_key
        self.name = name

    def create(self, *, timeout: float, **payload: Any) -> ModelResponse:
        pinned: str = payload["model"]
        requested, reported = (
            OPENROUTER_MODELS[pinned] if self.name == "openrouter" else (pinned, pinned)
        )

        async def request() -> ModelResponse:
            async with httpx.AsyncClient(trust_env=False, timeout=timeout) as client:
                async with client.stream(
                    "POST", JEV_URLS[self.name], json={**payload, "model": requested},
                    headers={"Authorization": "Bearer " + self._api_key},
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1_048_576:
                            raise ValueError("Routing response too large")
                    raw = json.loads(body)
                    usage = raw.get("usage", {})
                    try:
                        tokens = Usage(usage["input_tokens"], 0, usage["output_tokens"], 0)
                    except (KeyError, TypeError, ValueError):
                        tokens = None
                    # Only the exact reported snapshot counts as the pinned model. Any other
                    # name reaches the gateway unchanged, which bills it as drift.
                    model = str(raw.get("model", ""))
                    return ModelResponse(
                        str(raw.get("id") or response.headers.get("x-request-id", "")),
                        pinned if model == reported else model,
                        "completed", json.dumps(raw.get("answers")), [], tokens,
                    )

        async def bounded() -> ModelResponse:
            return await asyncio.wait_for(request(), timeout=timeout)

        return asyncio.run(bounded())


def provider_error(error: BaseException) -> str:
    if isinstance(error, (APITimeoutError, TimeoutError, httpx.TimeoutException)):
        return "model_timeout"
    if isinstance(error, RateLimitError):
        if error.code in {"insufficient_quota", "credit_balance_exhausted"} or (
            error.type == "insufficient_quota"
        ):
            return "model_quota_exhausted"
        return "rate_limited"
    if isinstance(error, APIConnectionError):
        return "model_unreachable"
    if isinstance(error, APIStatusError):
        return "model_provider_error"
    return "model_call_uncertain"


def wire_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return wire_value(value.model_dump())
    if isinstance(value, dict):
        return {key: wire_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise PaidCallError("unsupported_model_input")


def input_bound(payload: dict[str, Any]) -> int:
    """Text-only byte ceiling with doubled content and generous framing overhead.

    Never use characters/4: Unicode, schemas, tools, and replayed reasoning items
    must be included. This deliberately over-reserves; measured usage releases
    the difference. Enforced context bounds keep this below long-context rates.
    """
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return 2 * len(serialized.encode("utf-8")) + 8192


@dataclass
class TurnUsage:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        return {
            "modelCalls": len(self.calls),
            "inputTokens": sum(c.get("input_tokens", 0) for c in self.calls),
            "cachedInputTokens": sum(c.get("cached_input_tokens", 0) for c in self.calls),
            "outputTokens": sum(c.get("output_tokens", 0) for c in self.calls),
            "reasoningTokens": sum(c.get("reasoning_tokens", 0) for c in self.calls),
            "draftModelMs": sum(c["elapsedMs"] for c in self.calls if c["category"] == "draft"),
            "routingCalls": sum(c["category"] == "routing" for c in self.calls),
            "routingModelMs": sum(c["elapsedMs"] for c in self.calls if c["category"] == "routing"),
            "reviewModelMs": sum(c["elapsedMs"] for c in self.calls if c["category"] == "review"),
            "costNusd": sum(c.get("costNusd", 0) for c in self.calls),
            "unsettledNusd": sum(c["reservedNusd"] for c in self.calls if not c.get("settled")),
            "usageComplete": all(c.get("settled", False) for c in self.calls),
        }


class PaidGateway:
    def __init__(
        self,
        provider: Provider,
        ledger: Ledger,
        request_id: str,
        *,
        release: Release = RELEASE,
        project: str = "",
        routing_provider: JevProvider | None = None,
        config_hash: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(CAMPUS_ZONE),
    ) -> None:
        self._provider = provider
        self._routing_provider = routing_provider
        self._ledger = ledger
        self.request_id = request_id
        self.release = release
        self.project = project
        self.config_hash = config_hash or configuration_hash()
        self.clock = clock
        self.usage = TurnUsage()
        self.budget = TurnBudget(release)

    def finish(self, summary: dict[str, Any]) -> None:
        self._ledger.record_turn(
            self.request_id,
            {
                **summary,
                **self.usage.report(),
                "configurationHash": self.config_hash,
            },
        )

    def route(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        response = self.create(category="routing", **payload, timeout=timeout)
        answers = json.loads(response.output_text)
        if not isinstance(answers, dict):
            raise ValueError("Invalid routing answers")
        return answers

    def create(self, *, category: Category, **kwargs: Any) -> ModelResponse:
        started = monotonic()
        now = self.clock()
        routing = category == "routing"
        price = self.release.routing.price if routing else self.release.price
        today = now.astimezone(CAMPUS_ZONE).date()
        if not price.valid_from <= today < price.valid_until:
            raise PaidCallError("routing_price_unavailable" if routing else "price_unavailable")
        if category not in {"draft", "review", "routing"}:
            raise PaidCallError("unsupported_model_operation")
        available = self.budget.model_timeout(category)
        provider_name: str = self.release.provider
        if routing:
            if self._routing_provider is None:
                raise PaidCallError("routing_unavailable")
            provider_name = self._routing_provider.name
            if set(kwargs) != {"model", "state", "questions", "timeout"} or (
                kwargs["model"] != self.release.routing.model
            ):
                raise PaidCallError("unsupported_model_operation")
            timeout_value = kwargs["timeout"]
            if type(timeout_value) not in {int, float} or not math.isfinite(timeout_value) or (
                not 0 < timeout_value <= self.release.routing.timeout_seconds
            ):
                raise PaidCallError("unsupported_model_operation")
            kwargs["timeout"] = min(timeout_value, available)
            payload = wire_value({key: value for key, value in kwargs.items() if key != "timeout"})
            questions = payload["questions"]
            if not isinstance(questions, dict) or not questions or any(
                input_bound({"state": payload["state"], "question": question}) > 32000
                for question in questions.values()
            ):
                raise PaidCallError("routing_context_limit")
            output_limit = 0
            effort = "none"
        else:
            allowed = {
                "model",
                "instructions",
                "input",
                "tools",
                "tool_choice",
                "text",
                "parallel_tool_calls",
                "reasoning",
                "max_output_tokens",
                "store",
                "timeout",
            }
            if set(kwargs) - allowed or kwargs.get("model") != self.release.model:
                raise PaidCallError("unsupported_model_operation")
            output_limit = (
                self.release.draft_output_tokens
                if category == "draft"
                else self.release.review_output_tokens
            )
            if kwargs.get("max_output_tokens") != output_limit or kwargs.get("store") is not False:
                raise PaidCallError("unsupported_model_operation")
            timeout = kwargs.get("timeout")
            if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
                timeout = Timeout(timeout, connect=min(2.0, timeout))
            if not isinstance(timeout, Timeout) or any(
                value is None or not math.isfinite(value) or not 0 < value <= maximum
                for value, maximum in (
                    (timeout.read, self.release.turn_seconds),
                    (timeout.connect, 2.0),
                    (timeout.write, self.release.turn_seconds),
                    (timeout.pool, self.release.turn_seconds),
                )
            ):
                raise PaidCallError("unsupported_model_operation")
            assert timeout.read is not None and timeout.connect is not None
            kwargs["timeout"] = Timeout(
                min(float(timeout.read), available), connect=min(float(timeout.connect), available)
            )
            effort = (
                self.release.draft_effort(self.budget.draft_calls)
                if category == "draft"
                else self.release.review_reasoning
            )
            kwargs["reasoning"] = {"effort": effort}
            # Exclude network timeouts from token estimation; include every wire content field.
            payload = wire_value({key: value for key, value in kwargs.items() if key != "timeout"})
            if any(tool.get("type") != "function" for tool in payload.get("tools", [])):
                raise PaidCallError("unsupported_model_operation")

            def validate_text(item: Any) -> None:
                if isinstance(item, dict):
                    if item.get("type") in {"input_image", "input_file", "input_audio"}:
                        raise PaidCallError("unsupported_model_input")
                    for value in item.values():
                        validate_text(value)
                elif isinstance(item, list):
                    for value in item:
                        validate_text(value)

            validate_text(payload)
        bound = input_bound(payload)
        usage = self.usage.report()
        reserved = self.budget.admit_cost(
            category, bound, usage["costNusd"] + usage["unsettledNusd"]
        )
        operation_id = str(uuid4())
        metadata = {
            "provider": provider_name,
            "project": "" if routing else self.project,
            "requested_model": self.release.routing.model if routing else self.release.model,
            "configuration_hash": self.config_hash,
            "release_version": self.release.version,
            "price": price.model_dump(mode="json"),
            "input_token_bound": bound,
            "max_output_tokens": output_limit,
            "reasoning_effort": effort,
        }
        self._ledger.reserve(operation_id, self.request_id, category, reserved, metadata, now)
        item: dict[str, Any] = {
            "operationId": operation_id,
            "category": category,
            "reservedNusd": reserved,
            "elapsedMs": 0,
            "settled": False,
        }
        self.usage.calls.append(item)
        self.budget.note_model(category)
        try:
            # Explicit default service tier prevents priority-rate overrides. No truncation,
            # previous-response retrieval, built-in tools, or hidden conversation state.
            if routing:
                assert self._routing_provider is not None
                remaining = kwargs["timeout"] - (monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("Routing deadline exceeded")
                response = self._routing_provider.create(**payload, timeout=remaining)
                # TypeSafe does not promise a response ID. This is explicitly a local
                # receipt reference, never misrepresented as a provider-issued ID.
                if not response.id:
                    response.id = "local-operation:" + operation_id
            else:
                response = self._provider.create(
                    **payload, timeout=kwargs["timeout"],
                    service_tier="default", truncation="disabled"
                )
            item["elapsedMs"] = round((monotonic() - started) * 1000)
            if response.usage is None or not response.id:
                raise PaidCallError("usage_unknown")
            usage = asdict(response.usage)
            cost = response.usage.cost(price)
            try:
                self._ledger.settle(
                    operation_id,
                    cost,
                    usage,
                    response.id,
                    response.model,
                    item["elapsedMs"],
                    self.clock(),
                )
            except PaidCallError as error:
                if error.code == "accounting_bound_exceeded":
                    item.update(usage, costNusd=cost, settled=True)
                raise
            item.update(usage, costNusd=cost, settled=True)
            if routing and response.model != self.release.routing.model:
                raise PaidCallError("routing_model_changed")
            if not routing and response.model not in {self.release.model, "gpt-5.4-2026-03-05"}:
                self._ledger.pause()
                raise PaidCallError("model_identity_changed")
            if response.usage.input_tokens > bound or (
                not routing and response.usage.output_tokens > output_limit
            ):
                self._ledger.pause()
                raise PaidCallError("accounting_bound_exceeded")
            return response
        except BaseException as error:
            item["elapsedMs"] = round((monotonic() - started) * 1000)
            code = error.code if isinstance(error, PaidCallError) else (
                "routing_provider_error" if routing else provider_error(error)
            )
            if routing and code == "usage_unknown":
                code = "routing_usage_unknown"
            item["error"] = code
            if not item["settled"]:
                # If this update fails, the original durable reservation still holds.
                self._ledger.uncertain(operation_id, code, item["elapsedMs"])
            if isinstance(error, PaidCallError):
                if routing and error.code == "usage_unknown":
                    raise PaidCallError(code) from error
                raise
            if not isinstance(error, Exception):
                raise
            raise PaidCallError(code) from error


@contextmanager
def open_gateway(deployment: Deployment, request_id: str) -> Iterator[PaidGateway]:
    ledger = PostgresLedger(deployment.ledger_url, deployment.environment)
    with ledger.session():
        ledger.readiness()
        with OpenAI(
            api_key=deployment.api_key,
            project=deployment.project,
            max_retries=0,
            timeout=RELEASE.turn_seconds,
            base_url="https://api.openai.com/v1",
        ) as client:
            yield PaidGateway(
                OpenAIProvider(client), ledger, request_id, project=deployment.project,
                routing_provider=(JevProvider(deployment.routing_api_key,
                                              deployment.routing_provider)
                                  if deployment.routing_mode != "off"
                                  and deployment.routing_api_key else None),
            )
