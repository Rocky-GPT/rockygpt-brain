"""Thin wrapper around the OpenAI Chat Completions API.

Kept deliberately narrow (one `complete` call in, one `ModelTurn` out) so
the tool-calling loop and answer validation in orchestrator.py can be
tested against a fake implementation of `ModelClient` without a network
dependency or SDK-internal mocking.

The entire provider interaction is bounded, not just a single SDK attempt:
`max_retries` on the SDK client is capped so a single `complete` call can
retry at most once (retry/backoff beyond that belongs in the
orchestrator's own bounded tool-calling loop, not hidden inside an SDK that
could otherwise silently multiply a single request's latency past the UI's
60-second budget). Anything short of a well-formed response — an empty
`choices` list, a message missing expected attributes, a response with
neither content nor a tool call, or any other SDK/protocol anomaly — is
normalized to a single `ServiceUnavailableError` rather than leaking an
`IndexError`/`AttributeError`/`UnicodeEncodeError` out of this module.

Out-of-bounds or malformed fields **fail closed rather than being
truncated**: a tool-call id/name/arguments string that is empty, non-string,
or over its limit, or too many tool calls, raises `ServiceUnavailableError`
for the whole turn. Silently truncating a tool name could turn it into a
different, valid tool name; truncating an id risks collisions; truncating a
JSON arguments string produces invalid JSON that would fail downstream
anyway but for the wrong, confusing reason. A short, uniform rejection is
safer than any of those.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

from openai import APIError, APITimeoutError, AsyncOpenAI
from openai.types.chat import (
    ChatCompletionMessageFunctionToolCall,
    ChatCompletionMessageParam,
    ChatCompletionToolChoiceOptionParam,
    ChatCompletionToolParam,
)

from rockygpt_brain.errors import ServiceUnavailableError

DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 1

MAX_CONTENT_BYTES = 20_000
MAX_TOOL_CALLS = 16
MAX_TOOL_CALL_ID_LENGTH = 128
MAX_TOOL_CALL_NAME_LENGTH = 128
MAX_ARGUMENTS_BYTES = 16_000

_MALFORMED_RESPONSE = "The model provider returned an unexpected response."


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class ModelTurn:
    content: str | None
    tool_calls: list[ToolCall]


class ModelClient(Protocol):
    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        force_tool: str | None = None,
    ) -> ModelTurn: ...

    async def aclose(self) -> None: ...


def _utf8_byte_length(text: str) -> int:
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        # A lone surrogate or other unencodable content is malformed
        # provider output, not a 500-worthy bug in this module.
        raise ServiceUnavailableError(_MALFORMED_RESPONSE) from exc


class OpenAIModelClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 0,
    ) -> None:
        if not (0 < timeout_seconds <= MAX_TIMEOUT_SECONDS):
            raise ValueError(f"timeout_seconds must be in (0, {MAX_TIMEOUT_SECONDS}]")
        if not (0 <= max_retries <= MAX_RETRIES):
            raise ValueError(f"max_retries must be in [0, {MAX_RETRIES}]")
        self._client = AsyncOpenAI(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
        )
        self._model = model

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        force_tool: str | None = None,
    ) -> ModelTurn:
        tool_choice: ChatCompletionToolChoiceOptionParam = "auto"
        if force_tool is not None:
            tool_choice = {"type": "function", "function": {"name": force_tool}}

        try:
            # `messages`/`tools` are kept as plain `list[dict[str, Any]]` on
            # this class's own public surface (ModelClient Protocol) so
            # brain/tools.py and the test fakes stay decoupled from the
            # OpenAI SDK's TypedDicts; the cast below only asserts, at this
            # one call site, that what we built matches the shape the SDK
            # expects — which brain/tools.py's `openai_tool_specs()` and
            # orchestrator.py's message construction already guarantee at
            # runtime.
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=cast(list[ChatCompletionMessageParam], messages),
                tools=cast(list[ChatCompletionToolParam], tools),
                tool_choice=tool_choice,
            )
        except APITimeoutError as exc:
            raise ServiceUnavailableError("The model provider timed out.") from exc
        except APIError as exc:
            raise ServiceUnavailableError("The model provider is unavailable.") from exc

        try:
            message = response.choices[0].message
            content = message.content
            raw_tool_calls: list[ToolCall] = []
            for call in message.tool_calls or []:
                # This codebase only ever advertises "function"-type tools
                # (brain/tools.py, brain/answer.py); a "custom"-type tool
                # call is not something we asked for and has no `.function`
                # to read a name/arguments from, so treat it the same as
                # any other malformed/unexpected response shape.
                if not isinstance(call, ChatCompletionMessageFunctionToolCall):
                    raise ServiceUnavailableError(_MALFORMED_RESPONSE)
                raw_tool_calls.append(
                    ToolCall(
                        id=call.id,
                        name=call.function.name,
                        arguments_json=call.function.arguments,
                    )
                )
        except (IndexError, AttributeError, KeyError, TypeError) as exc:
            raise ServiceUnavailableError(_MALFORMED_RESPONSE) from exc

        if content is not None:
            if not isinstance(content, str) or _utf8_byte_length(content) > MAX_CONTENT_BYTES:
                raise ServiceUnavailableError(_MALFORMED_RESPONSE)

        if not content and not raw_tool_calls:
            raise ServiceUnavailableError(_MALFORMED_RESPONSE)

        if len(raw_tool_calls) > MAX_TOOL_CALLS:
            raise ServiceUnavailableError(_MALFORMED_RESPONSE)
        for tool_call in raw_tool_calls:
            _require_valid_tool_call(tool_call)

        return ModelTurn(content=content, tool_calls=raw_tool_calls)


def _require_valid_tool_call(call: ToolCall) -> None:
    if not isinstance(call.id, str) or not call.id or len(call.id) > MAX_TOOL_CALL_ID_LENGTH:
        raise ServiceUnavailableError(_MALFORMED_RESPONSE)
    if (
        not isinstance(call.name, str)
        or not call.name
        or len(call.name) > MAX_TOOL_CALL_NAME_LENGTH
    ):
        raise ServiceUnavailableError(_MALFORMED_RESPONSE)
    if not isinstance(call.arguments_json, str) or not call.arguments_json:
        raise ServiceUnavailableError(_MALFORMED_RESPONSE)
    if _utf8_byte_length(call.arguments_json) > MAX_ARGUMENTS_BYTES:
        raise ServiceUnavailableError(_MALFORMED_RESPONSE)
