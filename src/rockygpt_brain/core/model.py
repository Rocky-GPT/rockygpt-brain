"""The one model call. A question goes in, an answer comes out."""

from __future__ import annotations

import json
from typing import Any, Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.errors import ServiceError

_ANSWER = """Answer the question.

`currentTime` is the authority on today's date and time. Do not work either out yourself.

`earlierTurns` is what has already been said in this conversation. Use it only to work out
what a follow-up refers to.

Keep suggested questions short."""


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    answer: str
    suggested_questions: list[str] = Field(default_factory=list, alias="suggestedQuestions")


class ModelPort(Protocol):
    configured: bool

    async def answer(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
        style_mode: str | None,
        response_mode: str | None,
    ) -> Draft: ...


class OpenAIModel:
    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self.configured = bool(api_key) or client is not None
        self._model = model
        self._client = client or (AsyncOpenAI(api_key=api_key) if api_key else None)

    async def answer(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
        style_mode: str | None,
        response_mode: str | None,
    ) -> Draft:
        if self._client is None:
            raise ServiceError(
                503, "SERVICE_UNAVAILABLE", "OPENAI_API_KEY is not configured.", retryable=True
            )
        payload = {
            "question": question,
            "earlierTurns": context,
            "currentTime": current_time,
            "styleMode": style_mode,
            "responseMode": response_mode,
        }
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=_ANSWER,
                input=json.dumps(payload, default=str),
                text_format=Draft,
                store=False,
            )
            if response.output_parsed is None:
                raise ValueError("empty structured response")
            return Draft.model_validate(response.output_parsed)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "The answer service is temporarily unavailable.",
                retryable=True,
            ) from exc
