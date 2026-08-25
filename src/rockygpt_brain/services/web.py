"""The web, as the brain sees it.

The second outbound call that is not the pipeline's own two, and the only one
that leaves campus. It answers a `current` GENERAL question — one whose answer
changes between askings — and nothing else.

Every fact comes back with the page it came from, because an answer built from
the open web is only worth as much as what a reader can check.
"""

from __future__ import annotations

from typing import Any, Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

_SEARCH = """Search the web and answer the query.

Return one entry per fact that answers it. `source` is the page URL the fact
came from, on its own with no surrounding text. `publishedAt` is the date that
page gives for the fact, or null when it gives none.

Return nothing rather than a fact no page supports."""


class WebUnavailable(Exception):
    """The search did not happen. The turn continues without it."""


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    fact: str
    source: str
    published_at: str | None = Field(alias="publishedAt")


class Found(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    results: list[Fact]


class WebPort(Protocol):
    configured: bool

    async def search(self, query: str) -> list[dict[str, Any]]: ...


class OpenAIWeb:
    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self.configured = bool(api_key) or client is not None
        self._model = model
        self._client = client or (AsyncOpenAI(api_key=api_key) if api_key else None)

    async def search(self, query: str) -> list[dict[str, Any]]:
        if self._client is None:
            raise WebUnavailable("no web search is configured")
        try:
            response = await self._client.responses.parse(
                model=self._model,
                tools=[{"type": "web_search"}],
                instructions=_SEARCH,
                input=query,
                text_format=Found,
                store=False,
            )
            if response.output_parsed is None:
                raise ValueError("empty structured response")
            found = Found.model_validate(response.output_parsed)
        except Exception as exc:
            raise WebUnavailable(str(exc)) from exc
        return [item.model_dump(by_alias=True) for item in found.results]
