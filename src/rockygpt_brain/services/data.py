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

    async def transportation(self, query: dict[str, Any]) -> list[dict[str, Any]]: ...

    async def calendar(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def clubs(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def directory(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def locations(self, query: dict[str, str]) -> list[dict[str, Any]]: ...

    async def programs(self, query: dict[str, str]) -> list[dict[str, Any]]: ...


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

    async def transportation(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        """The same lookup `shuttle` makes. The capability was renamed, not the data."""
        return await self.shuttle(query)

    async def calendar(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._records(await self._get("/v1/search/academic-dates", query), "calendar")

    async def clubs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._records(await self._get("/v1/search/clubs", query), "clubs")

    async def programs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return self._records(await self._get("/v1/search/programs", query), "programs")

    async def directory(self, query: dict[str, str]) -> list[dict[str, Any]]:
        """Contacts, from the one endpoint that does not answer in `records`.

        It answers in buckets — offices, faculty and staff, everyone else — and
        `allContacts` is the three of them already merged. Which bucket a
        contact came from is the service's business; a capability asked for
        people and gets people.
        """
        return self._under(await self._get("/v1/directory", query), "allContacts", "directory")

    async def locations(self, query: dict[str, str]) -> list[dict[str, Any]]:
        """The campus map, which also answers in its own key rather than `records`."""
        return self._under(await self._get("/v1/map", query), "locations", "locations")

    @staticmethod
    def _under(body: dict[str, Any], key: str, capability: str) -> list[dict[str, Any]]:
        """Rows from a response that names them something other than `records`."""
        rows = body.get(key)
        if not isinstance(rows, list):
            raise DataUnavailable(f"the {capability} response carried no {key}")
        return [row for row in rows if isinstance(row, dict)]

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
