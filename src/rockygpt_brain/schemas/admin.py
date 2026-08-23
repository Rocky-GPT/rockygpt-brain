"""GET /v1/admin/logs* request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from rockygpt_brain.schemas.common import IDENTIFIER_PATTERN, QuestionOrigin, StrictModel


class LogCitation(StrictModel):
    title: str
    url: str


class ExtractedFact(StrictModel):
    key: str
    kind: str
    value: Any = None


class ChatLogItem(StrictModel):
    id: str
    session_id: str
    visitor_id: str | None = None
    user_message: str
    assistant_message: str
    route: str
    question_origin: QuestionOrigin | None = None
    tools_invoked: list[str]
    tool_arguments: dict[str, Any]
    citations: list[LogCitation]
    facts_extracted: list[ExtractedFact]
    debug_info: dict[str, Any] | None = None
    latency_ms: int = Field(ge=0)
    feedback: Literal["positive", "negative"] | None = None
    feedback_rating: Literal[-1, 1] | None = None
    feedback_category: str | None = None
    feedback_comment: str | None = None
    created_at: datetime


class LogMetrics(StrictModel):
    total_logs: int = Field(ge=0, alias="totalLogs")
    avg_latency_ms: float = Field(ge=0, alias="avgLatencyMs")
    unique_sessions: int = Field(ge=0, alias="uniqueSessions")
    unique_visitors: int | None = Field(default=None, ge=0, alias="uniqueVisitors")
    error_count: int = Field(ge=0, alias="errorCount")
    client_count: int = Field(ge=0, alias="clientCount")
    dev_count: int = Field(ge=0, alias="devCount")
    bot_count: int = Field(ge=0, alias="botCount")


class LogListResponse(StrictModel):
    logs: list[ChatLogItem] = Field(max_length=100)
    metrics: LogMetrics
    version: str


class UnmodifiedResponse(StrictModel):
    modified: Literal[False]


class OperatorFeedbackRequest(StrictModel):
    log_id: str = Field(
        min_length=1, max_length=128, pattern=IDENTIFIER_PATTERN, alias="logId"
    )
    feedback: Literal["positive", "negative"] | None = None
