from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class RagUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Passage:
    content: str
    domain: str
    title: str
    url: str


class RagPort(Protocol):
    async def retrieve(self, topic: str, limit: int) -> list[Passage]: ...


class HttpRag:
    def __init__(self, base_url: str, timeout: float, client: Any | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    async def retrieve(self, topic: str, limit: int) -> list[Passage]:
        body = await self._post("/v2/retrieve", {"query": topic, "topK": limit})
        records = body.get("records")
        if not isinstance(records, list):
            raise RagUnavailable("the retrieval response carried no records")
        sources = {
            item["evidenceId"]: item
            for item in body.get("evidence", [])
            if isinstance(item, dict) and "evidenceId" in item
        }
        passages: list[Passage] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            source = next(
                (sources[eid] for eid in record.get("evidenceIds", []) if eid in sources),
                None,
            )
            url = str(source.get("url", "")) if source else ""
            if not url:
                continue
            passages.append(
                Passage(
                    content=str(record.get("content", "")),
                    domain=str(record.get("domain", "")),
                    title=str(source.get("title", "")) if source else "" or url,
                    url=url,
                )
            )
        return passages

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
            raise RagUnavailable(str(exc)) from exc
        if not isinstance(body, dict):
            raise RagUnavailable("the retrieval service returned an unexpected body")
        return body
