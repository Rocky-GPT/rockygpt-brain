"""Retrieving passages from campus documents.

The RAG lane's one outbound call. It returns passages already paired with the
page each came from, because an answer quoted out of a document is worth
exactly what a reader can check — the same reason the web lane returns sources.

Every passage is scraped text. The retrieval service says so itself, marking
each chunk untrusted, and this file keeps that true: nothing here reads the
content, matches on it, or lets it decide anything. It is carried onward as
material to answer from and nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class RagUnavailable(Exception):
    """The retrieval did not happen. Different from finding nothing."""


@dataclass(frozen=True, slots=True)
class Passage:
    """One retrieved passage, and the page it came from."""

    content: str
    domain: str
    title: str
    url: str


class RagPort(Protocol):
    async def retrieve(self, topic: str, limit: int) -> list[Passage]: ...


class HttpRag:
    """The retrieval service as it stands today.

    The ranking behind this is early: a query and its close rewording return
    different numbers of passages, and a low-scoring irrelevant chunk can come
    back alongside a good one. That is a problem for the index, not for the
    brain — everything above this class is written against `RagPort`, so a
    better retriever replaces this and nothing else.
    """

    def __init__(self, base_url: str, timeout: float, client: Any | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    async def retrieve(self, topic: str, limit: int) -> list[Passage]:
        body = await self._post("/v2/retrieve", {"query": topic, "topK": limit})
        records = body.get("records")
        if not isinstance(records, list):
            raise RagUnavailable("the retrieval response carried no records")
        # Sources arrive in their own array, joined to a passage by id. A
        # passage whose source is missing is dropped rather than shown without
        # one: an answer nobody can check is what this lane exists to avoid.
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
