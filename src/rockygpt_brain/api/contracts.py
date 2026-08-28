from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    field_validator,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Identifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]
BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


HISTORY_EXCHANGES = 10
HISTORY_MESSAGES = HISTORY_EXCHANGES * 2


class ChatTurn(ContractModel):
    role: Literal["user", "assistant"]
    content: BoundedText


class ChatRequest(ContractModel):
    message: BoundedText
    history: Annotated[list[ChatTurn], Field(max_length=HISTORY_MESSAGES)] | None = None
    style_mode: Annotated[
        str | None, StringConstraints(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    ] = Field(default=None, alias="styleMode")
    response_mode: Annotated[
        str | None, StringConstraints(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    ] = Field(default=None, alias="responseMode")
    timezone: Annotated[str | None, StringConstraints(min_length=1, max_length=100)] = None
    conversation_id: Identifier | None = Field(default=None, alias="conversationId")
    visitor_id: Identifier | None = Field(default=None, alias="visitorId")
    now: datetime | None = None
    question_origin: Literal["client", "dev", "bot"] | None = Field(
        default=None, alias="questionOrigin"
    )

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must contain non-whitespace characters")
        return value


class Citation(ContractModel):
    source_id: Annotated[str | None, StringConstraints(max_length=256)] = Field(
        default=None, alias="sourceId"
    )
    title: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    url: HttpUrl
    source_path: Annotated[str | None, StringConstraints(max_length=1024)] = Field(
        default=None, alias="sourcePath"
    )
    snippet: Annotated[str | None, StringConstraints(max_length=1000)] = None
    collected_at: datetime | None = Field(default=None, alias="collectedAt")


class UiActionType(StrEnum):
    VIEW_MENU = "VIEW_MENU"
    VIEW_BUS = "VIEW_BUS"
    VIEW_PRINT = "VIEW_PRINT"
    VIEW_EVENTS = "VIEW_EVENTS"
    VIEW_MAP = "VIEW_MAP"
    VIEW_DIRECTORY = "VIEW_DIRECTORY"


class UiAction(ContractModel):
    type: UiActionType
    payload: dict[str, str] | None = None


class BrainTrace(ContractModel):
    question: dict[str, Any]
    memory: dict[str, Any]
    understanding: dict[str, Any]
    context: dict[str, Any]
    plan: dict[str, Any]
    normalized_plan: dict[str, Any] = Field(alias="normalizedPlan")
    execution: dict[str, Any]
    answer: dict[str, Any]


class ChatSuccess(ContractModel):
    request_id: Identifier = Field(alias="requestId")
    answer: str = Field(min_length=1)
    route: str = Field(min_length=1, max_length=64)
    citations: list[Citation]
    ui_actions: list[UiAction] = Field(alias="uiActions")
    suggested_questions: list[Annotated[str, StringConstraints(min_length=1, max_length=120)]] = (
        Field(alias="suggestedQuestions", max_length=10)
    )
    brain_trace: BrainTrace = Field(alias="brainTrace")

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answer must contain non-whitespace characters")
        return value


FeedbackCategory = Literal["inaccurate", "incomplete", "could_be_better", "outdated", "other"]


class FeedbackRequest(ContractModel):
    request_id: Identifier = Field(alias="requestId")
    rating: Literal[-1, 1]
    category: FeedbackCategory | None = None
    comments: Annotated[str | None, StringConstraints(max_length=2000)] = None


class FeedbackSuccess(ContractModel):
    success: Literal[True] = True


ErrorCode = Literal[
    "INVALID_REQUEST",
    "PAYLOAD_TOO_LARGE",
    "UNAUTHORIZED",
    "RATE_LIMITED",
    "DATASET_UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "INTERNAL_ERROR",
    "NOT_FOUND",
]


class ErrorDetail(ContractModel):
    code: ErrorCode
    message: str = Field(min_length=1, max_length=500)
    retryable: bool
    retry_after_seconds: int | None = Field(default=None, alias="retryAfterSeconds", ge=1)


class ErrorResponse(ContractModel):
    request_id: Identifier = Field(alias="requestId")
    error: ErrorDetail


class Health(ContractModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    status: Literal["healthy", "ok"]
    service: str | None = None
    uptime_seconds: float | None = Field(default=None, alias="uptimeSeconds", ge=0)


class Readiness(ContractModel):
    status: Literal["ready", "unready"]
    failing: list[str] | None = None
    # Subsystems that are broken without stopping the service from serving.
    # Kept apart from `failing` on purpose: the UI treats a non-2xx readiness
    # as the whole deployment being down, so putting chat-log storage in
    # `failing` would take the site off the air over a logging outage.
    degraded: list[str] | None = None
    timestamp: datetime | None = None


class LogCitation(ContractModel):
    title: str
    url: HttpUrl


class ExtractedFact(ContractModel):
    key: str
    kind: str
    value: Any


class ChatLogItem(ContractModel):
    id: str
    session_id: str
    visitor_id: str | None = None
    user_message: str
    assistant_message: str
    route: str
    question_origin: Literal["client", "dev", "bot"] | None = None
    tools_invoked: list[str]
    tool_arguments: dict[str, Any]
    citations: list[LogCitation]
    facts_extracted: list[ExtractedFact]
    debug_info: dict[str, Any] | None = None
    latency_ms: int = Field(ge=0)
    feedback: Literal["positive", "negative"] | None
    feedback_rating: Literal[-1, 1] | None
    feedback_category: str | None
    feedback_comment: str | None
    created_at: datetime


class LogMetrics(ContractModel):
    total_logs: int = Field(alias="totalLogs", ge=0)
    avg_latency_ms: float = Field(alias="avgLatencyMs", ge=0)
    unique_sessions: int = Field(alias="uniqueSessions", ge=0)
    unique_visitors: int | None = Field(default=None, alias="uniqueVisitors", ge=0)
    error_count: int = Field(alias="errorCount", ge=0)
    client_count: int = Field(alias="clientCount", ge=0)
    dev_count: int = Field(alias="devCount", ge=0)
    bot_count: int = Field(alias="botCount", ge=0)


class LogListResponse(ContractModel):
    logs: list[ChatLogItem] = Field(max_length=100)
    metrics: LogMetrics
    version: str


class UnmodifiedResponse(ContractModel):
    modified: Literal[False] = False


class OperatorFeedbackRequest(ContractModel):
    log_id: Identifier = Field(alias="logId")
    feedback: Literal["positive", "negative"] | None
