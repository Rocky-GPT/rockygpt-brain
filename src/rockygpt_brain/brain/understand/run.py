"""Ask BRAIN #1 what the question is asking."""

from __future__ import annotations

from typing import Any, Protocol

from rockygpt_brain.brain.prompt import beside
from rockygpt_brain.brain.understand.schema import Understanding
from rockygpt_brain.services.openai import StructuredModel

UNDERSTAND = beside(__file__)


class UnderstandPort(Protocol):
    configured: bool

    async def understand(
        self, question: str, context: list[dict[str, Any]], current_time: str
    ) -> Understanding: ...


class OpenAIUnderstand:
    """The only stage shown the conversation."""

    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self._model = StructuredModel(
            api_key, model, "The planning service is temporarily unavailable.", client
        )
        self.configured = self._model.configured

    async def understand(
        self, question: str, context: list[dict[str, Any]], current_time: str
    ) -> Understanding:
        return await self._model.parse(
            UNDERSTAND,
            {"question": question, "earlierTurns": context, "currentTime": current_time},
            Understanding,
        )
