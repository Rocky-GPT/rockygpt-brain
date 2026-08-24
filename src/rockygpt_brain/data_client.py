"""The BASE brain's two calls to RockyGPT DATA."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import httpx

from rockygpt_brain.errors import ServiceError
from rockygpt_brain.model import Intent


class DataPort(Protocol):
    async def shuttle(self, intent: Intent, now: datetime) -> dict[str, Any]: ...

    async def retrieve(self, query: str, domains: list[str]) -> dict[str, Any]: ...

    async def readiness(self) -> bool: ...


class DataClient:
    def __init__(
        self,
        base_url: str,
        environment_token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=8.0)
        self._headers = (
            {"x-rockygpt-environment-token": environment_token} if environment_token else {}
        )

    async def shuttle(self, intent: Intent, now: datetime) -> dict[str, Any]:
        scopes = {
            "first": "full_day",
            "next": "remaining",
            "current": "at_time",
            "all": "full_day",
        }
        body: dict[str, Any] = {
            "asOf": now.isoformat(),
            "selection": intent.selection,
            "timeScope": scopes[intent.selection],
        }
        for name in ("route", "origin", "destination"):
            value = getattr(intent, name)
            if value:
                body[name] = value
        if intent.service_date:
            body["serviceDate"] = intent.service_date.isoformat()
        return await self._post("/v2/capabilities/shuttle/query", body)

    async def retrieve(self, query: str, domains: list[str]) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query}
        if domains:
            body["domains"] = domains
        return await self._post("/v2/retrieve", body)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=body, headers=self._headers)
            if response.status_code == 400:
                raise ServiceError(400, "INVALID_REQUEST", "The request is invalid.")
            if response.status_code != 200:
                raise ServiceError(
                    503,
                    "DATASET_UNAVAILABLE",
                    "Campus data is temporarily unavailable.",
                    retryable=True,
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("DATA response is not an object")
            return payload
        except ServiceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceError(
                503,
                "DATASET_UNAVAILABLE",
                "Campus data is temporarily unavailable.",
                retryable=True,
            ) from exc

    async def readiness(self) -> bool:
        try:
            response = await self._client.get("/readiness")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()
