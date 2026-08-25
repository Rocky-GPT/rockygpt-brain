"""Ask BRAIN #1 what the question is asking.

The only stage shown the conversation.

Before editing `prompt.md`: it is the highest-churn, highest-risk text in the
brain, and a sentence added to it has twice moved lane routing on questions it
was not about. Measure before and after. It describes steps, not questions —
no phrase, entity, or example from any test belongs in it, because prose added
to fix one question reliably breaks three others.
"""

from __future__ import annotations

from typing import Any, Protocol

from rockygpt_brain.brain.understand.schema import Understanding
from rockygpt_brain.prompt import beside
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
