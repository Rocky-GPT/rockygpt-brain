"""The two AI calls used by the BASE brain."""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from rockygpt_brain.api.contracts import ChatTurn
from rockygpt_brain.errors import ServiceError


class Lane(StrEnum):
    CODE = "code"
    RAG = "rag"
    MEMORY = "memory"
    GENERAL = "general"
    SAFETY = "safety"


CodeAction = Literal[
    "campus_hours",
    "dining_hours",
    "menu",
    "contacts",
    "clubs",
    "events",
    "programs",
    "academic_dates",
    "map",
    "shuttle",
]


class Intent(BaseModel):
    """Structured intent returned by AI #1."""

    lane: Lane
    action: CodeAction | None = None
    selection: Literal["first", "next", "current", "all"] = "next"
    day: (
        Literal[
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        | None
    ) = None
    meal: str | None = None
    route: str | None = None
    origin: str | None = None
    destination: str | None = None
    service_date: date | None = Field(default=None, alias="serviceDate")
    query: str | None = None
    domains: list[str] = Field(default_factory=list)


class Draft(BaseModel):
    """Natural-language response returned by AI #2."""

    answer: str
    suggested_questions: list[str] = Field(
        default_factory=list,
        alias="suggestedQuestions",
    )


class ModelPort(Protocol):
    configured: bool

    async def understand(
        self,
        message: str,
        history: list[ChatTurn],
        memory: list[dict[str, Any]],
        now: datetime,
    ) -> Intent: ...

    async def communicate(
        self,
        message: str,
        intent: Intent,
        result: dict[str, Any],
        style_mode: str | None,
        response_mode: str | None,
    ) -> Draft: ...


StructuredOutput = TypeVar("StructuredOutput", Intent, Draft)


class OpenAIModel:
    """One structured call to understand, then one to communicate."""

    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self.configured = bool(api_key) or client is not None
        self._model = model
        self._client = client or (AsyncOpenAI(api_key=api_key) if api_key else None)

    async def understand(
        self,
        message: str,
        history: list[ChatTurn],
        memory: list[dict[str, Any]],
        now: datetime,
    ) -> Intent:
        return await self._parse(
            Intent,
            (
                "You are RockyGPT AI #1: UNDERSTAND. Choose exactly one lane. "
                "Use code for objective campus data: campus_hours, dining_hours, menu, "
                "contacts, clubs, events, programs, academic_dates, map, or shuttle. Use rag "
                "for campus documents, policies, and prose. Use memory for questions about this "
                "conversation. Use general for non-campus knowledge. Use safety for urgent "
                "danger or crisis requests. For code, set query only to an actual search term; "
                "leave it empty for broad requests such as today's menu. Extract only useful "
                "fields."
            ),
            {
                "message": message,
                "history": [turn.model_dump() for turn in history],
                "memory": memory,
                "now": now.isoformat(),
            },
        )

    async def communicate(
        self,
        message: str,
        intent: Intent,
        result: dict[str, Any],
        style_mode: str | None,
        response_mode: str | None,
    ) -> Draft:
        return await self._parse(
            Draft,
            (
                "You are RockyGPT AI #2: COMMUNICATE. Write a clear human answer from the "
                "provided result JSON. For code results, report what code returned and do not "
                "calculate new facts. For RAG results, use only the retrieved records. For "
                "memory results, use only the supplied turns. General results may be answered "
                "from your general knowledge. Keep suggested questions short."
            ),
            {
                "message": message,
                "intent": intent.model_dump(mode="json", by_alias=True),
                "result": result,
                "styleMode": style_mode,
                "responseMode": response_mode,
            },
        )

    async def _parse(
        self,
        output_type: type[StructuredOutput],
        instructions: str,
        payload: dict[str, Any],
    ) -> StructuredOutput:
        if self._client is None:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "OPENAI_API_KEY is not configured.",
                retryable=True,
            )
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=instructions,
                input=json.dumps(payload, default=str),
                text_format=output_type,
                store=False,
            )
            if response.output_parsed is None:
                raise ValueError("empty structured response")
            return output_type.model_validate(response.output_parsed)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "The answer service is temporarily unavailable.",
                retryable=True,
            ) from exc
