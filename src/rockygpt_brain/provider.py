"""One bounded provider gateway for chat and evaluations.

Reserve before sending, settle usage before interpreting output, and retain holds
when delivery or usage is uncertain. Prompts and provider error text never enter
ledger metadata or public exceptions.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from openai import APIStatusError, APITimeoutError, AsyncOpenAI

from rockygpt_brain.settings import ProviderSettings
from rockygpt_brain.spending import Ledger, PostgresLedger, Reservation, SpendingError
from rockygpt_brain.timing import measure

LOG = logging.getLogger(__name__)


class GatewayError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class NotSentError(Exception):
    """An adapter has proved that it did not attempt an HTTP request."""


@dataclass
class TurnBudget:
    request_id: str
    deadline: float
    max_calls: int = 4
    max_cost_nusd: int = 25_000_000
    calls: int = 0
    committed_nusd: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int

    def as_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cached_tokens": self.cached_tokens}


@dataclass(frozen=True)
class Completion:
    text: str
    tool_calls: tuple[ToolCall, ...]
    output: list[dict[str, Any]]
    usage: Usage


class Transport(Protocol):
    async def send(self, request: dict[str, Any], remaining_seconds: float) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class OpenAITransport:
    def __init__(self, settings: ProviderSettings) -> None:
        self._client = AsyncOpenAI(
            api_key=settings.api_key, project=settings.project, max_retries=0,
            base_url="https://api.openai.com/v1",
        )

    async def send(self, request: dict[str, Any], remaining_seconds: float) -> dict[str, Any]:
        response = await self._client.responses.create(**request, timeout=remaining_seconds)
        # Optional output fields may be null even when their continuation-input
        # counterparts require omission (e.g. reasoning.content/status). Preserve
        # encrypted reasoning and string-encoded tool arguments without adding
        # those invalid wrapper nulls to the next request.
        return response.model_dump(exclude_none=True)  # type: ignore[no-any-return]

    async def close(self) -> None:
        await self._client.close()


def _integer(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GatewayError("provider_invalid_response")
    return value


def _usage(response: dict[str, Any]) -> Usage:
    try:
        raw = response["usage"]
        input_tokens = _integer(raw["input_tokens"])
        output_tokens = _integer(raw["output_tokens"])
        details = raw.get("input_tokens_details") or {}
        cached_tokens = _integer(details.get("cached_tokens", 0))
        if cached_tokens > input_tokens:
            raise GatewayError("provider_invalid_response")
        return Usage(input_tokens, output_tokens, cached_tokens)
    except (KeyError, TypeError, AttributeError) as error:
        raise GatewayError("provider_invalid_response") from error


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate tool argument")
        result[key] = value
    return result


def _nonfinite_json(value: str) -> None:
    raise ValueError("nonfinite tool argument")


def _completion(response: dict[str, Any], usage: Usage) -> Completion:
    if response.get("status") != "completed":
        raise GatewayError("provider_incomplete")
    output = response.get("output")
    if not isinstance(output, list) or not output:
        raise GatewayError("provider_invalid_response")
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    call_ids: set[str] = set()
    for item in output:
        if not isinstance(item, dict):
            raise GatewayError("provider_invalid_response")
        if item.get("type") == "function_call":
            call_id, name = item.get("call_id"), item.get("name")
            raw_arguments = item.get("arguments")
            if (not isinstance(call_id, str) or not call_id or call_id in call_ids
                    or not isinstance(name, str) or not name or not isinstance(raw_arguments, str)):
                raise GatewayError("provider_invalid_response")
            try:
                arguments = json.loads(
                    raw_arguments, object_pairs_hook=_json_object, parse_constant=_nonfinite_json,
                )
            except ValueError as error:
                raise GatewayError("provider_invalid_response") from error
            if not isinstance(arguments, dict):
                raise GatewayError("provider_invalid_response")
            call_ids.add(call_id)
            calls.append(ToolCall(call_id, name, arguments))
        elif item.get("type") == "message":
            if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                raise GatewayError("provider_invalid_response")
            for part in item["content"]:
                if not isinstance(part, dict):
                    raise GatewayError("provider_invalid_response")
                if part.get("type") == "refusal":
                    raise GatewayError("provider_refused")
                if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                    raise GatewayError("provider_invalid_response")
                text_parts.append(part["text"])
        elif item.get("type") != "reasoning":
            raise GatewayError("provider_invalid_response")
    if not calls and not any(text_parts):
        raise GatewayError("provider_invalid_response")
    return Completion("\n".join(text_parts), tuple(calls), output, usage)


class Gateway:
    def __init__(
        self, settings: ProviderSettings, *, ledger: Ledger | None = None,
        transport: Transport | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.ledger = ledger if ledger is not None else PostgresLedger(
            settings.ledger_url, settings.environment,
        )
        self.transport = transport if transport is not None else OpenAITransport(settings)
        self._now = now
        self._monotonic = monotonic

    async def open(self) -> None:
        if isinstance(self.ledger, PostgresLedger):
            await self.ledger.open()

    async def close(self) -> None:
        await self.transport.close()
        if isinstance(self.ledger, PostgresLedger):
            await self.ledger.close()

    async def ready(self) -> bool:
        return self.settings.prices.valid(self._now()) and await self.ledger.ready()

    async def _uncertain(self, reservation: Reservation, code: str) -> None:
        try:
            with measure("Record uncertain model charge"):
                await self.ledger.uncertain(reservation, code)
        except SpendingError:
            # The original reserved row still consumes the full allowance.
            pass

    async def complete(
        self, *, input: list[dict[str, Any]], tools: list[dict[str, Any]], budget: TurnBudget,
    ) -> Completion:
        async with budget._lock:
            try:
                return await self._complete(input=input, tools=tools, budget=budget)
            except SpendingError as error:
                raise GatewayError(error.code) from error

    async def _complete(
        self, *, input: list[dict[str, Any]], tools: list[dict[str, Any]], budget: TurnBudget,
    ) -> Completion:
        settings = self.settings
        if not settings.prices.valid(self._now()):
            raise GatewayError("model_not_configured")
        if budget.calls >= budget.max_calls:
            raise GatewayError("turn_limit")
        if self._monotonic() >= budget.deadline:
            raise GatewayError("deadline_exceeded")
        request: dict[str, Any] = {
            "model": settings.prices.model, "input": input, "tools": tools,
            "max_output_tokens": settings.max_output_tokens, "store": False,
            "service_tier": "default",
            "include": ["reasoning.encrypted_content"],
        }
        if tools:
            request.update(tool_choice="required", parallel_tool_calls=False)
        try:
            serialized = json.dumps(request, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError) as error:
            raise GatewayError("invalid_model_input") from error
        byte_count = len(serialized.encode("utf-8"))
        if byte_count > settings.max_input_bytes:
            raise GatewayError("context_limit")
        # One token per UTF-8 byte is conservative for text. The envelope margin
        # covers provider-internal framing; actual overruns pause the account.
        input_bound = byte_count + 1_024
        reserve_cost = settings.prices.cost(input_bound, settings.max_output_tokens)
        if budget.committed_nusd + reserve_cost > budget.max_cost_nusd:
            raise GatewayError("turn_budget_exhausted")
        with measure("Reserve model allowance"):
            reservation = await self.ledger.reserve(
                budget.request_id, reserve_cost,
                {"provider": "openai", "project": settings.project,
                 "price": settings.prices.metadata(), "input_token_bound": input_bound,
                 "max_output_tokens": settings.max_output_tokens}, self._now(),
            )
        budget.committed_nusd += reserve_cost
        remaining = budget.deadline - self._monotonic()
        if remaining <= 0 or not settings.prices.valid(self._now()):
            with measure("Release unused model allowance"):
                await self.ledger.release(reservation, "not_sent_deadline_or_price", self._now())
            budget.committed_nusd -= reserve_cost
            raise GatewayError("deadline_exceeded" if remaining <= 0 else "model_not_configured")
        budget.calls += 1
        try:
            async with asyncio.timeout(remaining):
                with measure("Provider request · network and model"):
                    response = await self.transport.send(request, remaining)
        except NotSentError as error:
            with measure("Release unused model allowance"):
                await self.ledger.release(reservation, "not_sent", self._now())
            budget.committed_nusd -= reserve_cost
            raise GatewayError("provider_unavailable", retryable=True) from error
        except asyncio.CancelledError:
            # Cancellation must stop promptly. The durable reserved row already
            # holds the full cost and is reconciled just like an uncertain row;
            # do not delay disconnect cleanup with another database round trip.
            raise
        except Exception as error:
            status = error.status_code if isinstance(error, APIStatusError) else None
            if isinstance(error, (TimeoutError, APITimeoutError)) or status == 408:
                code, retryable = "model_timeout", True
            elif status is not None and 400 <= status < 500 and status != 429:
                code, retryable = "provider_request_rejected", False
            else:
                code, retryable = "provider_unavailable", True
            # Error bodies/messages/headers can contain student text or secrets.
            # Restrict diagnostics to code-controlled names and numeric status.
            LOG.warning(
                "provider_failure request_id=%s exception_type=%s http_status=%s code=%s",
                budget.request_id, type(error).__name__, status, code,
            )
            await self._uncertain(reservation, code)
            raise GatewayError(code, retryable=retryable) from error
        try:
            with measure("Validate model usage"):
                usage = _usage(response)
        except GatewayError:
            await self._uncertain(reservation, "invalid_usage")
            raise
        actual = settings.prices.cost(usage.input_tokens, usage.output_tokens, usage.cached_tokens)
        response_id, returned_model = response.get("id"), response.get("model")
        if returned_model != settings.prices.model:
            # The configured rates cannot price an unexpected model. Preserve
            # the full hold and pause admission for operator reconciliation.
            await self._uncertain(reservation, "provider_model_mismatch")
            with measure("Pause model spending"):
                await self.ledger.pause()
            raise GatewayError("provider_model_mismatch")
        # Charge known usage even when the rest of the response is malformed.
        with measure("Settle model usage"):
            await self.ledger.settle(
                reservation, actual, usage.as_dict(),
                response_id if isinstance(response_id, str) else "",
                returned_model if isinstance(returned_model, str) else "", self._now(),
            )
        budget.committed_nusd += actual - reserve_cost
        if (actual > reserve_cost or usage.output_tokens > settings.max_output_tokens
                or usage.input_tokens > input_bound):
            with measure("Pause model spending"):
                await self.ledger.pause()
            raise GatewayError("provider_usage_exceeded")
        with measure("Decode model decision"):
            return _completion(response, usage)
