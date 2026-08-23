import pytest
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.api.parsing import parse_json_body, read_bounded_body
from rockygpt_brain.errors import InvalidRequestError, PayloadTooLargeError


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=50)


def _make_request(body: bytes, *, headers: dict[str, str] | None = None) -> Request:
    header_list = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat",
        "headers": header_list,
    }
    sent = {"done": False}

    async def receive() -> dict[str, object]:
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


async def test_valid_json_parses() -> None:
    request = _make_request(b'{"name": "library"}')
    result = await parse_json_body(request, _Model, max_bytes=1024)
    assert result.name == "library"


async def test_empty_body_rejected() -> None:
    request = _make_request(b"")
    with pytest.raises(InvalidRequestError):
        await parse_json_body(request, _Model, max_bytes=1024)


async def test_invalid_json_syntax_rejected() -> None:
    request = _make_request(b"{not valid json")
    with pytest.raises(InvalidRequestError):
        await parse_json_body(request, _Model, max_bytes=1024)


async def test_invalid_utf8_bytes_rejected_as_invalid_request() -> None:
    # A lone continuation byte is invalid UTF-8; json.loads must not be
    # allowed to raise a bare UnicodeDecodeError past this boundary.
    request = _make_request(b"\xff\xfe\x00")
    with pytest.raises(InvalidRequestError):
        await parse_json_body(request, _Model, max_bytes=1024)


async def test_json_scalar_body_rejected() -> None:
    for scalar in (b"true", b"42", b'"just a string"', b"null"):
        request = _make_request(scalar)
        with pytest.raises(InvalidRequestError):
            await parse_json_body(request, _Model, max_bytes=1024)


async def test_unknown_field_rejected() -> None:
    request = _make_request(b'{"name": "library", "evil": "x"}')
    with pytest.raises(InvalidRequestError):
        await parse_json_body(request, _Model, max_bytes=1024)


async def test_oversized_streamed_body_rejected_as_413() -> None:
    request = _make_request(b'{"name": "' + b"x" * 2000 + b'"}')
    with pytest.raises(PayloadTooLargeError):
        await parse_json_body(request, _Model, max_bytes=100)


async def test_oversized_declared_content_length_rejected_before_read() -> None:
    request = _make_request(b'{"name": "library"}', headers={"content-length": "999999"})
    with pytest.raises(PayloadTooLargeError):
        await read_bounded_body(request, max_bytes=100)


async def test_malformed_content_length_rejected() -> None:
    request = _make_request(b'{"name": "library"}', headers={"content-length": "not-a-number"})
    with pytest.raises(InvalidRequestError):
        await read_bounded_body(request, max_bytes=1024)


async def test_negative_content_length_rejected() -> None:
    request = _make_request(b'{"name": "library"}', headers={"content-length": "-5"})
    with pytest.raises(InvalidRequestError):
        await read_bounded_body(request, max_bytes=1024)


async def test_huge_unknown_field_name_produces_bounded_error_message() -> None:
    huge_key = "k" * 5000
    body = ('{"name": "library", "' + huge_key + '": 1}').encode()
    request = _make_request(body)
    with pytest.raises(InvalidRequestError) as excinfo:
        await parse_json_body(request, _Model, max_bytes=10_000)
    assert len(str(excinfo.value)) <= 320  # bounded well below the field's raw length
