"""The two AI calls used by the BASE brain."""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol, TypeAlias, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.core.capabilities import (
    CodeAction,
    SortMetric,
    TimeScope,
    capability_guide,
)
from rockygpt_brain.errors import ServiceError


class StrictModel(BaseModel):
    """Structured model output with no undeclared escape hatches."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Lane(StrEnum):
    CODE = "code"
    RAG = "rag"
    MEMORY = "memory"
    GENERAL = "general"
    SAFETY = "safety"


Day = Literal[
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


class SearchFilters(StrictModel):
    query: str | None = Field(
        default=None,
        description="A literal entity, title, name, or topic to search for; never an operation.",
    )


class HoursFilters(SearchFilters):
    day: Day | None = None


class MenuFilters(SearchFilters):
    meal: str | None = None


class ShuttleFilters(StrictModel):
    route: str | None = None
    origin: str | None = None
    destination: str | None = None
    service_date: date | None = Field(default=None, alias="serviceDate")


class SemanticOperation(StrictModel):
    """User-requested computation expressed without DATA implementation fields."""

    time_scope: TimeScope | None = Field(default=None, alias="timeScope")
    sort_by: SortMetric | None = Field(default=None, alias="sortBy")
    direction: Literal["ascending", "descending"] | None = None
    limit: int | None = Field(default=None, ge=1, le=50)


class HoursCodeRequest(StrictModel):
    action: Literal[CodeAction.CAMPUS_HOURS, CodeAction.DINING_HOURS]
    filters: HoursFilters = Field(default_factory=HoursFilters)
    operation: SemanticOperation | None = None


class MenuCodeRequest(StrictModel):
    action: Literal[CodeAction.MENU]
    filters: MenuFilters = Field(default_factory=MenuFilters)
    operation: SemanticOperation | None = None


class SearchCodeRequest(StrictModel):
    action: Literal[
        CodeAction.CONTACTS,
        CodeAction.CLUBS,
        CodeAction.EVENTS,
        CodeAction.PROGRAMS,
        CodeAction.ACADEMIC_DATES,
        CodeAction.MAP,
    ]
    filters: SearchFilters = Field(default_factory=SearchFilters)
    operation: SemanticOperation | None = None


class ShuttleCodeRequest(StrictModel):
    action: Literal[CodeAction.SHUTTLE]
    filters: ShuttleFilters = Field(default_factory=ShuttleFilters)
    operation: SemanticOperation | None = None


CodeRequest: TypeAlias = Annotated[
    HoursCodeRequest | MenuCodeRequest | SearchCodeRequest | ShuttleCodeRequest,
    Field(discriminator="action"),
]


class CodeIntent(StrictModel):
    lane: Literal[Lane.CODE]
    request: CodeRequest


class RagIntent(StrictModel):
    lane: Literal[Lane.RAG]
    query: str = Field(min_length=1)
    domains: list[str] = Field(default_factory=list)


class MemoryIntent(StrictModel):
    lane: Literal[Lane.MEMORY]
    query: str | None = None


class GeneralIntent(StrictModel):
    lane: Literal[Lane.GENERAL]


class SafetyIntent(StrictModel):
    lane: Literal[Lane.SAFETY]


LaneIntent: TypeAlias = Annotated[
    CodeIntent | RagIntent | MemoryIntent | GeneralIntent | SafetyIntent,
    Field(discriminator="lane"),
]


class Intent(StrictModel):
    """Strict AI #1 envelope with a different contract for every lane."""

    decision: LaneIntent

    @property
    def lane(self) -> Lane:
        return Lane(self.decision.lane)

    def trace(self) -> dict[str, Any]:
        """Expose a flat, readable IN object while keeping a strict model schema."""

        return self.decision.model_dump(mode="json", by_alias=True, exclude_none=True)


class Draft(StrictModel):
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
        history: list[dict[str, Any]],
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
        history: list[dict[str, Any]],
        now: datetime,
    ) -> Intent:
        return await self._parse(
            Intent,
            (
                "You are RockyGPT AI #1: UNDERSTAND. Classify only currentQuestion and return "
                "one strict decision. referenceContext is not another task: use it only when "
                "currentQuestion explicitly depends on an earlier subject, pronoun, or statement. "
                "A self-contained currentQuestion always replaces the earlier topic. "
                "Choose code for objective campus data, rag for campus policies/documents/prose, "
                "memory only when the answer must recall what was said in this conversation, "
                "general for non-campus knowledge, and safety only for immediate physical danger "
                "or crisis. A campus follow-up remains code or rag; context alone never makes it "
                "memory. For code, express meaning rather than implementation: filters contain "
                "only literal entity values, sortBy is a semantic concept, and Python chooses all "
                "DATA field paths. Never put ranking, timing, or comparison instructions into a "
                "query filter. If the user asks for a concept DATA may not support, represent that "
                "concept honestly; Python will report whether it is executable. Use timeScope only "
                "where the capability guide allows it. Do not copy fields from referenceContext.\n"
                "Executable CODE capability guide:\n"
                f"{capability_guide()}"
            ),
            {
                "referenceContext": history,
                "currentTime": now.isoformat(),
                "currentQuestion": message,
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
                "calculate new facts. If code reports an unsupported operation, say that the "
                "available campus data cannot answer it; never substitute an arbitrary record. "
                "For RAG results, use only the retrieved records. For memory results, use only "
                "the supplied turns. General results may be answered from your general knowledge. "
                "Keep suggested questions short."
            ),
            {
                "message": message,
                "intent": intent.trace(),
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
