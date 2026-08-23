"""Async HTTP client for the campus data service.

The brain's *only* sanctioned path to campus data (spec/system-boundaries.md:
"Do not scrape campus sites from the brain, import data-service code, query
its database, use development inspector routes"). Every method here maps to
one row of the "Preferred answer-facing endpoints" table in
spec/data-service.md.

Every response is read through `_read_bounded`, which streams the body
(`stream=True` + `aiter_bytes`) rather than letting httpx buffer it whole,
so `MAX_RESPONSE_BYTES` is an actual memory cap rather than a check applied
after the full body is already in memory. An honest-but-oversized
`Content-Length` is rejected before any body bytes are read; a chunked or
lying body is aborted mid-stream the moment the cumulative cap is exceeded.
A timeout/transport failure *during* streaming (not just while establishing
the response) is normalized to `DataServiceUnavailable` the same way a
failure during `send` is.

The upstream response is closed on every path via `try/finally`. That close
can itself fail: if an exception is already propagating (a cap rejection, a
contract error, a read-time transport failure), a close-time failure is
suppressed so it cannot replace/mask the original; if nothing was already
propagating, a close-time failure is instead raised as a fresh
`DataServiceUnavailable` rather than silently swallowed. Both the success
path and the error-envelope path (`_raise_for_error_response`) decode JSON
from those same bounded bytes — an error body gets exactly the same size
cap as a success body, not a separate unbounded read.

The error envelope itself is treated as untrusted input, not a trusted
internal signal: `_raise_for_error_response` requires an exact, bounded
object shape with the exact primitive types the contract specifies, honors
the data service's documented `code` enum
(`spec/data-api.openapi.yaml`'s `ApiError`) only when it is consistent with
the actual HTTP status observed (`NOT_FOUND` only on a real 404, `UNAVAILABLE`
only on a real 5xx) so a mismatched code/status pair from an untrusted or
buggy upstream cannot change local semantics, and never surfaces the
upstream `message` text or the response status in the fallback message —
every fallback uses one fixed string.

`SearchResult`/`SafetyResources`/`Dataset`/`Source` parsing
(data_client/models.py) is itself strict and raises `DataContractError` on
any contract mismatch, so a malformed or oversized upstream response fails
this one call closed instead of reaching business logic as if it were
trustworthy structured data.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

import httpx

from rockygpt_brain.data_client.errors import (
    DataClientError,
    DataContractError,
    DataServiceUnavailable,
)
from rockygpt_brain.data_client.models import SafetyResources, SearchResult

# A header name, not a credential value.
ENVIRONMENT_TOKEN_HEADER = "x-rockygpt-environment-token"  # noqa: S105

_DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
MAX_RESPONSE_BYTES = 2_000_000

# The data service's own documented error-code enum (spec/data-api.openapi
# .yaml ApiError.error.code) — not this brain's ErrorDetail.code vocabulary.
_KNOWN_DATA_ERROR_CODES = frozenset({"NOT_FOUND", "INVALID_REQUEST", "UNAVAILABLE"})
_FALLBACK_ERROR_MESSAGE = "Campus data request failed."


@dataclass(frozen=True, slots=True)
class _BoundedResponse:
    status_code: int
    body: bytes


class DataServiceClient:
    def __init__(
        self,
        *,
        base_url: str,
        environment_token: str | None = None,
        timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {}
        if environment_token:
            headers[ENVIRONMENT_TOKEN_HEADER] = environment_token
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        response = await self._request("GET", "/health")
        return _response_json_object(response)

    async def readiness(self) -> dict[str, Any]:
        response = await self._request("GET", "/readiness", allow_statuses={200, 503})
        return _response_json_object(response)

    async def search_campus_hours(
        self, *, q: str | None = None, day: str | None = None, at: str | None = None
    ) -> SearchResult:
        return await self._search("/v1/search/campus-hours", {"q": q, "day": day, "at": at})

    async def search_dining_hours(
        self, *, q: str | None = None, day: str | None = None, at: str | None = None
    ) -> SearchResult:
        return await self._search("/v1/search/dining-hours", {"q": q, "day": day, "at": at})

    async def search_menu(
        self, *, q: str | None = None, meal: str | None = None, at: str | None = None
    ) -> SearchResult:
        return await self._search("/v1/search/menu", {"q": q, "meal": meal, "at": at})

    async def search_contacts(
        self, *, q: str | None = None, at: str | None = None
    ) -> SearchResult:
        return await self._search("/v1/search/contacts", {"q": q, "at": at})

    async def search_clubs(self, *, q: str | None = None, at: str | None = None) -> SearchResult:
        return await self._search("/v1/search/clubs", {"q": q, "at": at})

    async def search_events(
        self, *, q: str | None = None, at: str | None = None
    ) -> SearchResult:
        return await self._search("/v1/search/events", {"q": q, "at": at})

    async def search_programs(
        self, *, q: str | None = None, at: str | None = None
    ) -> SearchResult:
        return await self._search("/v1/search/programs", {"q": q, "at": at})

    async def search_academic_dates(
        self, *, q: str | None = None, at: str | None = None
    ) -> SearchResult:
        return await self._search("/v1/search/academic-dates", {"q": q, "at": at})

    async def search_shuttles(
        self,
        *,
        route: str | None = None,
        service_day: str | None = None,
        at: str | None = None,
    ) -> SearchResult:
        return await self._search(
            "/v1/search/shuttles", {"route": route, "serviceDay": service_day, "at": at}
        )

    async def safety_resources(self) -> SafetyResources:
        response = await self._request("GET", "/v1/safety-resources")
        return SafetyResources.from_json(_response_json(response))

    async def map(self, *, q: str | None = None) -> dict[str, Any]:
        response = await self._request("GET", "/v1/map", params=_clean_params({"q": q}))
        return _response_json_object(response)

    async def _search(self, path: str, params: dict[str, str | None]) -> SearchResult:
        response = await self._request("GET", path, params=_clean_params(params))
        return SearchResult.from_json(_response_json(response))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        allow_statuses: set[int] | None = None,
    ) -> _BoundedResponse:
        try:
            request = self._client.build_request(method, path, params=params)
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise DataServiceUnavailable("Campus data request timed out.") from exc
        except httpx.TransportError as exc:
            raise DataServiceUnavailable("Campus data is unreachable.") from exc

        body = await _read_bounded(response, max_bytes=MAX_RESPONSE_BYTES)
        bounded = _BoundedResponse(status_code=response.status_code, body=body)

        ok_statuses = allow_statuses or {200}
        if bounded.status_code in ok_statuses:
            return bounded
        _raise_for_error_response(bounded)
        return bounded


async def _read_bounded(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Stream `response`'s body up to `max_bytes`, closing it on every path.

    Rejects an honest `Content-Length` over the cap before reading any body
    bytes, and separately aborts mid-stream if cumulative bytes read exceed
    the cap regardless of what `Content-Length` claimed (a chunked or lying
    body). A malformed/negative `Content-Length` is treated the same as an
    oversized one rather than trusted. A timeout/transport failure while
    streaming — not just while establishing the response — is normalized to
    `DataServiceUnavailable`, the same as a failure during `send`.
    """
    try:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            if not content_length.isdigit():
                raise DataContractError("Campus data response has an invalid Content-Length.")
            if int(content_length) > max_bytes:
                raise DataContractError("Campus data response exceeded the allowed size.")

        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise DataContractError("Campus data response exceeded the allowed size.")
                chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise DataServiceUnavailable("Campus data request timed out.") from exc
        except httpx.TransportError as exc:
            raise DataServiceUnavailable("Campus data is unreachable.") from exc
        return b"".join(chunks)
    finally:
        # Preserve whether an exception was already propagating *before*
        # attempting to close, so a close-time failure can never replace it.
        exception_already_active = sys.exc_info()[0] is not None
        try:
            await response.aclose()
        except Exception as close_exc:
            if exception_already_active:
                pass  # never mask the exception already propagating
            else:
                raise DataServiceUnavailable(
                    "Campus data response could not be closed cleanly."
                ) from close_exc


def _response_json(response: _BoundedResponse) -> Any:
    try:
        return json.loads(response.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DataContractError("Campus data response was not valid JSON.") from exc


def _response_json_object(response: _BoundedResponse) -> dict[str, Any]:
    """Like `_response_json`, but for the loosely-typed passthrough
    endpoints (`/health`, `/readiness`, `/v1/map`) that don't have their
    own strict `from_json` parser: validates the parsed JSON is actually an
    object rather than returning `Any` (and silently mismatching the
    declared `dict[str, Any]` return type if the data service ever sent a
    JSON array or scalar for one of these)."""
    parsed = _response_json(response)
    if not isinstance(parsed, dict):
        raise DataContractError("Campus data response must be a JSON object.")
    return parsed


def _clean_params(params: dict[str, str | None]) -> dict[str, str]:
    return {key: value for key, value in params.items() if value is not None}


def _parse_known_error_code(body: bytes) -> str | None:
    """Return the data service's error `code` only if the envelope matches
    its documented shape exactly (spec/data-api.openapi.yaml `ApiError`) and
    the code is one of the small known enum values — otherwise None. The
    `message` field's *content* is validated for type only and is never
    read further; it is never surfaced by this client. Whether the code is
    actually honored still depends on the observed HTTP status — see
    `_raise_for_error_response`."""
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict) or parsed.keys() != {"error"}:
        return None
    error = parsed["error"]
    if not isinstance(error, dict) or error.keys() != {"code", "message", "retryable"}:
        return None
    code, message, retryable = error["code"], error["message"], error["retryable"]
    if not isinstance(code, str) or not isinstance(message, str) or not isinstance(retryable, bool):
        return None
    return code if code in _KNOWN_DATA_ERROR_CODES else None


def _raise_for_error_response(response: _BoundedResponse) -> None:
    if response.status_code == 503:
        raise DataServiceUnavailable()

    known_code = _parse_known_error_code(response.body)
    # A code is only honored when it is consistent with the actual HTTP
    # status observed — an untrusted/mismatched code/status pair (e.g.
    # code=NOT_FOUND on a 500) falls through to the fixed fallback below
    # rather than being allowed to change local semantics.
    if known_code == "NOT_FOUND" and response.status_code == 404:
        raise DataClientError(
            code="NOT_FOUND",
            message="The requested campus data resource was not found.",
            retryable=False,
            status_code=response.status_code,
        )
    if known_code == "UNAVAILABLE" and response.status_code >= 500:
        raise DataServiceUnavailable()

    raise DataClientError(
        code="SERVICE_UNAVAILABLE",
        message=_FALLBACK_ERROR_MESSAGE,
        retryable=response.status_code >= 500,
        status_code=response.status_code,
    )
