"""Strict JSON body parsing shared by every POST route.

Bypasses FastAPI/Starlette's default body handling so that invalid JSON,
invalid UTF-8, JSON scalars (`true`, `"x"`, `42`, `null`), unknown fields,
and over-limit values are all funneled through one path that raises the
app's own `InvalidRequestError`/`PayloadTooLargeError` (see errors.py)
rather than a framework-default 422 or an unhandled 500. The body is read
in bounded chunks so an oversized body is rejected without buffering
unbounded memory, independent of whether the client sends an honest
`Content-Length`.
"""

from __future__ import annotations

import json
from typing import TypeVar

from fastapi import Request
from pydantic import BaseModel, ValidationError

from rockygpt_brain.errors import InvalidRequestError, PayloadTooLargeError

T = TypeVar("T", bound=BaseModel)

MAX_CHAT_BODY_BYTES = 32_768
MAX_FEEDBACK_BODY_BYTES = 8_192
MAX_ADMIN_BODY_BYTES = 4_096

# Bounds on the reflected validation-error summary: `loc` components come
# from attacker-controlled JSON keys (e.g. an "extra inputs not permitted"
# error echoes the offending field name), so both each component and the
# final message are truncated regardless of how large the input was.
MAX_LOCATION_PART_LENGTH = 60
MAX_ERROR_MESSAGE_LENGTH = 200
MAX_SUMMARY_LENGTH = 300


async def read_bounded_body(request: Request, *, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        if not content_length.isdigit():
            raise InvalidRequestError("Content-Length header is malformed.")
        if int(content_length) > max_bytes:
            raise PayloadTooLargeError("Request body exceeds the allowed size.")

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLargeError("Request body exceeds the allowed size.")
        chunks.append(chunk)
    return b"".join(chunks)


async def parse_json_body(request: Request, model: type[T], *, max_bytes: int) -> T:
    body = await read_bounded_body(request, max_bytes=max_bytes)
    if not body.strip():
        raise InvalidRequestError("Request body must not be empty.")
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidRequestError("Request body is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise InvalidRequestError("Request body must be a JSON object.")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise InvalidRequestError(_summarize(exc)) from exc


def _summarize(exc: ValidationError) -> str:
    first = exc.errors()[0]
    parts = [str(part)[:MAX_LOCATION_PART_LENGTH] for part in first["loc"]]
    location = ".".join(parts) or "body"
    message = str(first["msg"])[:MAX_ERROR_MESSAGE_LENGTH]
    return f"{location}: {message}"[:MAX_SUMMARY_LENGTH]
