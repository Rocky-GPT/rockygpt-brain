"""Ask BRAIN #3 for the answer.

Last, and never concurrent with the lane. It writes from what PYTHON produced:
`grounding` carries `answerFrom` on every turn — campus rows, web results, the
safety response, or the model's own knowledge — so it never has to infer what
to do from a field that is not there.

Before editing `prompt.md`: `answerFrom` is an instruction, never a status. It
says where this answer comes from, not that anything is missing or unbuilt. A
lane with no executor must stay indistinguishable from a question that never
needed one — told a lookup failed, the model apologises for a capability
instead of answering the question.
"""

from __future__ import annotations

from typing import Any, Protocol

from rockygpt_brain.brain.write.schema import Draft
from rockygpt_brain.prompt import beside
from rockygpt_brain.services.openai import StructuredModel

ANSWER = beside(__file__)


class WritePort(Protocol):
    configured: bool

    async def answer(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
        style_mode: str | None,
        response_mode: str | None,
        grounding: dict[str, Any],
    ) -> Draft: ...


class OpenAIWrite:
    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self._model = StructuredModel(
            api_key, model, "The answer service is temporarily unavailable.", client
        )
        self.configured = self._model.configured

    async def answer(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
        style_mode: str | None,
        response_mode: str | None,
        grounding: dict[str, Any],
    ) -> Draft:
        return await self._model.parse(
            ANSWER,
            {
                "question": question,
                "earlierTurns": context,
                "currentTime": current_time,
                "styleMode": style_mode,
                "responseMode": response_mode,
                # Every lane grounds the answer, so this is spread in
                # unconditionally rather than being a branch.
                **grounding,
            },
            Draft,
        )
