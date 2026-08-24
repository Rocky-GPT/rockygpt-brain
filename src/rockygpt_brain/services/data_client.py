"""The BASE brain's two calls to RockyGPT DATA."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import httpx

from rockygpt_brain.errors import ServiceError

if TYPE_CHECKING:
    from rockygpt_brain.core.compilation import CompiledPlan


class DataPort(Protocol):
    async def execute(self, plan: CompiledPlan) -> dict[str, Any]: ...

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

    async def execute(self, plan: CompiledPlan) -> dict[str, Any]:
        """Send a compiled plan. The client chooses nothing.

        Every parameter was fixed during compilation, so there is no place here
        for a default, a fallback, or a filter decision.
        """

        if plan.capability.method == "POST":
            return await self._post(plan.capability.path, plan.body)
        result = await self._get(plan.capability.path, plan.params)
        return self._add_source_evidence(result)

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
