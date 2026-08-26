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

    async def dining(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def events(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def campus_hours(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def dining_hours(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def courses(self, query: dict[str, str]) -> list[dict[str, Any]]: ...


class HttpData:
    def __init__(self, base_url: str, timeout: float, client: Any | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        body = await self._post("/v2/capabilities/shuttle/query", query)
        return self._records(body, "shuttle")

    async def dining(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._records(await self._get("/v1/search/menu", query), "dining")

    async def events(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._records(await self._get("/v1/search/events", query), "events")

    async def campus_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._records(await self._get("/v1/search/campus-hours", query), "hours")

    async def dining_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._records(await self._get("/v1/search/dining-hours", query), "dining hours")

    async def courses(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._records(await self._get("/v1/search/courses", query), "courses")

    @staticmethod
    def _records(body: dict[str, Any], capability: str) -> list[dict[str, Any]]:
        records = body.get("records")
        if not isinstance(records, list):
            raise DataUnavailable(f"the {capability} response carried no records")
        return [record for record in records if isinstance(record, dict)]

    async def _get(self, path: str, query: dict[str, str]) -> dict[str, Any]:
        try:
            if self._client is not None:
                response = await self._client.get(f"{self._base}{path}", params=query)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(f"{self._base}{path}", params=query)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            raise DataUnavailable(str(exc)) from exc
        if not isinstance(body, dict):
            raise DataUnavailable("the data service returned an unexpected body")
        return body

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
