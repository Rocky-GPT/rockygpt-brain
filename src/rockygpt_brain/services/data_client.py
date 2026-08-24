"""The BASE brain's two calls to RockyGPT DATA."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import httpx

from rockygpt_brain.core.model import Intent
from rockygpt_brain.errors import ServiceError

_STRUCTURED_ROUTES: dict[str, tuple[str, dict[str, str], bool]] = {
    "campus_hours": ("/v1/search/campus-hours", {"query": "q", "day": "day"}, True),
    "dining_hours": ("/v1/search/dining-hours", {"query": "q", "day": "day"}, True),
    "menu": ("/v1/search/menu", {"query": "q", "meal": "meal"}, True),
    "contacts": ("/v1/search/contacts", {"query": "q"}, True),
    "clubs": ("/v1/search/clubs", {"query": "q"}, True),
    "events": ("/v1/search/events", {"query": "q"}, True),
    "programs": ("/v1/search/programs", {"query": "q"}, True),
    "academic_dates": ("/v1/search/academic-dates", {"query": "q"}, True),
    "map": ("/v1/map", {"query": "q"}, False),
}


class DataPort(Protocol):
    async def code(self, intent: Intent, now: datetime) -> dict[str, Any]: ...

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

    async def code(self, intent: Intent, now: datetime) -> dict[str, Any]:
        if intent.action == "shuttle":
            return await self._shuttle(intent, now)
        if intent.action not in _STRUCTURED_ROUTES:
            return {"outcome": "unsupported", "action": intent.action}

        path, fields, include_time = _STRUCTURED_ROUTES[intent.action]
        filters = self._filters(intent)
        params = {
            parameter: filters[field]
            for field, parameter in fields.items()
            if field in filters
        }
        if include_time:
            params["at"] = now.isoformat()
        result = await self._get(path, params)
        return self._add_source_evidence(result)

    async def _shuttle(self, intent: Intent, now: datetime) -> dict[str, Any]:
        filters = self._filters(intent)
        requested_scope = intent.operation.time_scope if intent.operation else None
        scopes = {
            "all": ("all", "full_day"),
            "remaining": ("all", "remaining"),
            "active": ("current", "at_time"),
        }
        selection, time_scope = scopes[requested_scope or "all"]
        body: dict[str, Any] = {
            "asOf": now.isoformat(),
            "selection": selection,
            "timeScope": time_scope,
        }
        for name in ("route", "origin", "destination"):
            value = filters.get(name)
            if value:
                body[name] = value
        if filters.get("serviceDate"):
            body["serviceDate"] = filters["serviceDate"]
        return await self._post("/v2/capabilities/shuttle/query", body)

    @staticmethod
    def _filters(intent: Intent) -> dict[str, str]:
        return {item.field: item.value for item in intent.filters if item.value.strip()}

    async def retrieve(self, query: str, domains: list[str]) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query}
        if domains:
            body["domains"] = domains
        return await self._post("/v2/retrieve", body)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, body=body)

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                path,
                json=body,
                params=params,
                headers=self._headers,
            )
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

    @staticmethod
    def _add_source_evidence(payload: dict[str, Any]) -> dict[str, Any]:
        records = payload.get("records")
        evidence: list[dict[str, Any]] = []
        seen: set[str] = set()
        if isinstance(records, list):
            for record in records:
                source = record.get("source") if isinstance(record, dict) else None
                if not isinstance(source, dict) or not source.get("sourceId"):
                    continue
                source_id = str(source["sourceId"])
                if source_id in seen:
                    continue
                seen.add(source_id)
                evidence.append({"evidenceId": f"source:{source_id}", **source})
        return {
            "outcome": "success" if records else "empty",
            **payload,
            "evidence": evidence,
        }

    async def readiness(self) -> bool:
        try:
            response = await self._client.get("/readiness")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()
