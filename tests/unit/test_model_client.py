"""Unit tests for OpenAIModelClient's response validation.

`client._client` (the real `AsyncOpenAI` instance) is replaced wholesale
with a small fake object exposing the same `chat.completions.create(...)`/
`close()` shape, rather than monkeypatching a method onto the real SDK's
internal resource objects. This sidesteps any assumption about whether the
installed `openai` SDK's internal *nested resource* classes support
instance-level attribute reassignment (not verifiable here without the
dependency installed) — `OpenAIModelClient._client` is a plain attribute on
a class this codebase owns, so swapping it is unconditionally supported.

Cleanup must not leak the real `AsyncOpenAI` instance that's constructed
and then replaced: `_close_model_client` closes the *original* client
explicitly whenever a test has swapped it out for a fake (since
`instance.aclose()` alone only ever reaches whatever `instance._client`
currently points to), then closes the client via the normal `aclose()`
path. The `client` fixture uses this helper in a `finally`, so it runs even
when a test body raises; `test_cleanup_helper_closes_both_original_and_
replacement` proves the mechanism itself actually closes both objects.
"""

from types import SimpleNamespace

import pytest
from openai import APIError, APITimeoutError
from openai.types.chat import ChatCompletionMessageFunctionToolCall
from openai.types.chat.chat_completion_message_function_tool_call import Function

from rockygpt_brain.brain.model_client import (
    MAX_ARGUMENTS_BYTES,
    MAX_CONTENT_BYTES,
    MAX_TOOL_CALLS,
    OpenAIModelClient,
)
from rockygpt_brain.errors import ServiceUnavailableError


class _FakeCompletions:
    def __init__(self, *, response: object = None, exception: Exception | None = None) -> None:
        self._response = response
        self._exception = exception

    async def create(self, **kwargs: object) -> object:
        if self._exception is not None:
            raise self._exception
        return self._response


class _FakeOpenAIClient:
    def __init__(self, *, response: object = None, exception: Exception | None = None) -> None:
        self.chat = SimpleNamespace(
            completions=_FakeCompletions(response=response, exception=exception)
        )
        self.closed = False

    async def close(self) -> None:
        self.closed = True


async def _close_model_client(instance: OpenAIModelClient, original_client: object) -> None:
    """Close whatever `instance._client` currently is (via the normal
    `aclose()` path) *and*, if a test replaced `_client` with something
    else, also close the original real SDK client explicitly — otherwise
    it would be silently orphaned rather than cleaned up."""
    if instance._client is not original_client:
        await original_client.close()
    await instance.aclose()


@pytest.fixture
async def client():
    instance = OpenAIModelClient(api_key="test-key", model="gpt-test")
    original_client = instance._client
    try:
        yield instance
    finally:
        await _close_model_client(instance, original_client)


def _fake_tool_call(
    call_id: str = "call_1", name: str = "submit_answer", arguments: str = "{}"
) -> ChatCompletionMessageFunctionToolCall:
    # A real SDK type, not a SimpleNamespace: model_client.py's fail-closed
    # response validation now requires `isinstance(call,
    # ChatCompletionMessageFunctionToolCall)` (it rejects the SDK's other
    # "custom"-type tool call, which has no `.function` to read a name/
    # arguments from), so a loose stand-in would no longer exercise the
    # code path these tests are about. SimpleNamespace is kept only for
    # `test_malformed_tool_call_missing_function_attr_raises`, which is
    # deliberately testing a call that *isn't* this type.
    return ChatCompletionMessageFunctionToolCall(
        id=call_id, type="function", function=Function(name=name, arguments=arguments)
    )


def _stub_response(content: str | None, tool_calls: list[object]) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


async def test_cleanup_helper_closes_both_original_and_replacement() -> None:
    instance = OpenAIModelClient(api_key="test-key", model="gpt-test")
    original_client = instance._client

    original_closed = {"value": False}
    real_close = original_client.close

    async def _spy_close() -> None:
        original_closed["value"] = True
        await real_close()

    original_client.close = _spy_close

    fake = _FakeOpenAIClient(response=_stub_response("hi", []))
    instance._client = fake

    await _close_model_client(instance, original_client)

    assert original_closed["value"] is True
    assert fake.closed is True


class TestValidResponses:
    async def test_content_only_response(self, client: OpenAIModelClient) -> None:
        client._client = _FakeOpenAIClient(response=_stub_response("hello", []))
        turn = await client.complete(messages=[], tools=[])
        assert turn.content == "hello"
        assert turn.tool_calls == []

    async def test_tool_call_response(self, client: OpenAIModelClient) -> None:
        client._client = _FakeOpenAIClient(response=_stub_response(None, [_fake_tool_call()]))
        turn = await client.complete(messages=[], tools=[])
        assert turn.content is None
        assert turn.tool_calls[0].name == "submit_answer"


class TestFailClosed:
    async def test_empty_choices_raises_service_unavailable(
        self, client: OpenAIModelClient
    ) -> None:
        client._client = _FakeOpenAIClient(response=SimpleNamespace(choices=[]))
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])

    async def test_neither_content_nor_tool_calls_raises(
        self, client: OpenAIModelClient
    ) -> None:
        client._client = _FakeOpenAIClient(response=_stub_response(None, []))
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])

    async def test_empty_string_content_with_no_tool_calls_raises(
        self, client: OpenAIModelClient
    ) -> None:
        client._client = _FakeOpenAIClient(response=_stub_response("", []))
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])

    async def test_oversized_content_raises_not_truncates(
        self, client: OpenAIModelClient
    ) -> None:
        huge_content = "x" * (MAX_CONTENT_BYTES + 1)
        client._client = _FakeOpenAIClient(response=_stub_response(huge_content, []))
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])

    async def test_too_many_tool_calls_raises(self, client: OpenAIModelClient) -> None:
        calls = [_fake_tool_call(call_id=f"c{i}") for i in range(MAX_TOOL_CALLS + 1)]
        client._client = _FakeOpenAIClient(response=_stub_response(None, calls))
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])

    async def test_empty_tool_call_id_raises(self, client: OpenAIModelClient) -> None:
        client._client = _FakeOpenAIClient(
            response=_stub_response(None, [_fake_tool_call(call_id="")])
        )
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])

    async def test_empty_arguments_json_raises(self, client: OpenAIModelClient) -> None:
        client._client = _FakeOpenAIClient(
            response=_stub_response(None, [_fake_tool_call(arguments="")])
        )
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])

    async def test_oversized_arguments_json_raises(self, client: OpenAIModelClient) -> None:
        huge_args = "x" * (MAX_ARGUMENTS_BYTES + 1)
        client._client = _FakeOpenAIClient(
            response=_stub_response(None, [_fake_tool_call(arguments=huge_args)])
        )
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])

    async def test_malformed_tool_call_missing_function_attr_raises(
        self, client: OpenAIModelClient
    ) -> None:
        broken_call = SimpleNamespace(id="c1")  # no .function at all
        client._client = _FakeOpenAIClient(response=_stub_response(None, [broken_call]))
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])

    async def test_provider_timeout_raises_service_unavailable(
        self, client: OpenAIModelClient
    ) -> None:
        client._client = _FakeOpenAIClient(
            exception=APITimeoutError(request=SimpleNamespace())
        )
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])

    async def test_provider_api_error_raises_service_unavailable(
        self, client: OpenAIModelClient
    ) -> None:
        client._client = _FakeOpenAIClient(
            exception=APIError(message="boom", request=SimpleNamespace(), body=None)
        )
        with pytest.raises(ServiceUnavailableError):
            await client.complete(messages=[], tools=[])


class TestConstructorBounds:
    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            OpenAIModelClient(api_key="k", model="m", timeout_seconds=0)

    def test_timeout_must_not_exceed_max(self) -> None:
        with pytest.raises(ValueError):
            OpenAIModelClient(api_key="k", model="m", timeout_seconds=31)

    def test_max_retries_must_not_be_negative(self) -> None:
        with pytest.raises(ValueError):
            OpenAIModelClient(api_key="k", model="m", max_retries=-1)

    def test_max_retries_bounded(self) -> None:
        with pytest.raises(ValueError):
            OpenAIModelClient(api_key="k", model="m", max_retries=2)
