from __future__ import annotations

from typing import Any, Protocol

from rockygpt_brain.brain.resolve.schema import Resolution
from rockygpt_brain.prompt import beside
from rockygpt_brain.services.openai import StructuredModel

RESOLVE = beside(__file__)


class ResolvePort(Protocol):
    configured: bool

    async def resolve(
        self,
        question: str,
        spans: list[str],
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Resolution: ...


class OpenAIResolve:
    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self._model = StructuredModel(
            api_key, model, "The planning service is temporarily unavailable.", client
        )
        self.configured = self._model.configured

    async def resolve(
        self,
        question: str,
        spans: list[str],
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Resolution:
        return await self._model.parse(
            RESOLVE,
            {
                "question": question,
                "spans": spans,
                "earlierTurns": context,
                "currentTime": current_time,
            },
            Resolution,
        )
