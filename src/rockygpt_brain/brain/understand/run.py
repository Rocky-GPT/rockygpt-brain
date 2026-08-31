from __future__ import annotations

from typing import Any, Protocol

from rockygpt_brain.brain.resolve.run import OpenAIResolve
from rockygpt_brain.brain.resolve.schema import Resolution
from rockygpt_brain.brain.understand.schema import Reading
from rockygpt_brain.prompt import beside
from rockygpt_brain.services.openai import StructuredModel

UNDERSTAND = beside(__file__)


class UnderstandPort(Protocol):
    """Two readings of one question, and the order they must happen in.

    `understand` never sees the conversation. That is the whole mechanism: a
    question that can be read alone cannot be coloured by an earlier turn,
    because there is no earlier turn to read. `resolve` sees it, and is given
    only the spans the first reading could not account for.
    """

    configured: bool

    async def understand(self, question: str, current_time: str) -> Reading: ...

    async def resolve(
        self,
        question: str,
        spans: list[str],
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Resolution: ...


class OpenAIUnderstand:
    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self._model = StructuredModel(
            api_key, model, "The planning service is temporarily unavailable.", client
        )
        self._resolver = OpenAIResolve(api_key, model, client)
        self.configured = self._model.configured

    async def understand(self, question: str, current_time: str) -> Reading:
        return await self._model.parse(
            UNDERSTAND,
            {"question": question, "currentTime": current_time},
            Reading,
        )

    async def resolve(
        self,
        question: str,
        spans: list[str],
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Resolution:
        return await self._resolver.resolve(question, spans, context, current_time)
