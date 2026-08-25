"""The data service, as the brain sees it.

The one outbound call BASE makes that is not a model call. It answers the CODE
lane and nothing else; when it cannot, the turn is still answered, without it.

One method per capability that has an executor. The method sends the data
service's own request shape — translating a plan into that shape is the
executor's job, not this file's.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx


class DataUnavailable(Exception):
    """The lookup did not happen. The turn continues without it."""


class DataPort(Protocol):
    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]: ...


class HttpData:
    def __init__(self, base_url: str, timeout: float, client: Any | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        body = await self._post("/v2/capabilities/shuttle/query", query)
        records = body.get("records")
        if not isinstance(records, list):
            raise DataUnavailable("the shuttle response carried no records")
        return [record for record in records if isinstance(record, dict)]

    async def _post(self, path: str, query: dict[str, Any]) -> dict[str, Any]:
        try:
            if self._client is not None:
                response = await self._client.post(f"{self._base}{path}", json=query)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(f"{self._base}{path}", json=query)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise DataUnavailable(str(exc)) from exc
        if not isinstance(body, dict):
            raise DataUnavailable("the data service returned an unexpected body")
        return body
