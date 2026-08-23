"""POST /v1/chat request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field

from rockygpt_brain.schemas.common import (
    IDENTIFIER_PATTERN,
    ChatTurn,
    Citation,
    QuestionOrigin,
    StrictModel,
    UiAction,
)

STYLE_MODE_PATTERN = r"^[A-Za-z0-9_-]+$"


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=10)
    style_mode: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=STYLE_MODE_PATTERN, alias="styleMode"
    )
    response_mode: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=STYLE_MODE_PATTERN,
        alias="responseMode",
    )
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=IDENTIFIER_PATTERN,
        alias="conversationId",
    )
    visitor_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=IDENTIFIER_PATTERN,
        alias="visitorId",
    )
    now: datetime | None = None
    question_origin: QuestionOrigin | None = Field(default=None, alias="questionOrigin")


class ChatSuccess(StrictModel):
    request_id: str = Field(
        min_length=1, max_length=128, pattern=IDENTIFIER_PATTERN, alias="requestId"
    )
    answer: str = Field(min_length=1)
    route: str = Field(min_length=1, max_length=64)
    citations: list[Citation]
    ui_actions: list[UiAction] = Field(alias="uiActions")
    suggested_questions: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=10, alias="suggestedQuestions"
    )
