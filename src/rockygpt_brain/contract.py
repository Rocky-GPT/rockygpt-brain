"""What the apps send the Brain and what it sends back. Nothing else crosses the wire.

The student app (rockygpt-ui) and the dev UI (rockygpt-dev) already speak this shape,
so a Brain that keeps it can replace the old one without either app changing.
docs/contract.md says the same in plain words.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Longest conversation the Brain reads, in characters over every message.
MAX_CONVERSATION_CHARACTERS = 48_000

# The universal help every failure carries, even when nothing else works.
EMERGENCY_TEXT = (
    "If you or someone else is in danger, call 911. If you might hurt yourself, "
    "call or text 988 (Suicide & Crisis Lifeline)."
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- What an app sends: POST /v1/chat ----


class ChatMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16_000)

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message content must not be blank")
        return value


class ChatRequest(StrictModel):
    """The visible conversation, oldest first, ending with the student's new question."""

    messages: list[ChatMessage] = Field(min_length=1, max_length=80)
    omittedMessages: int = Field(
        default=0,
        ge=0,
        le=100_000,
        description="How many earlier messages of the conversation the app left out, so "
        "the Brain never claims something wasn't said when it simply wasn't sent.",
    )

    @model_validator(mode="after")
    def whole_turn(self) -> "ChatRequest":
        if self.messages[0].role != "user" or self.messages[-1].role != "user":
            raise ValueError("Conversation must begin and end with a user message")
        if sum(len(message.content) for message in self.messages) > MAX_CONVERSATION_CHARACTERS:
            raise ValueError("Conversation is too long; start a new conversation")
        return self


# ---- What the Brain sends back ----


class Source(StrictModel):
    """A page a student can open. Links only ever come from records, never model text."""

    title: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")


class Citation(Source):
    """A record an answer relies on. The dev UI's Sources panel reads the optional fields."""

    id: str
    record_title: str | None = None
    collection: str | None = None
    collected_at: str | None = None
    freshness: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    trust_tier: str | None = None
    limitations: list[str] | None = None


class ChatReply(StrictModel):
    """HTTP 200: an answer. `answer` is markdown whose only links are its citations."""

    answer: str = Field(min_length=1, max_length=12_000)
    status: Literal["answered", "partial", "clarification", "unavailable"]
    citations: list[Citation]
    requestId: str
    datasetVersion: str | None = None
    # Development only, and only when the dev UI asks with x-rockygpt-diagnostics: 1.
    metrics: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None


class EmergencyHelp(StrictModel):
    text: str = Field(min_length=1)
    sources: list[Source] = []


ErrorCode = Literal[
    "not_ready",
    "busy",
    "rate_limited",
    "budget_exhausted",
    "model_quota_exhausted",
    "model_timeout",
    "model_unreachable",
    "model_provider_error",
    "model_not_configured",
    "invalid_model_output",
    # The campus data can't be read, or isn't trustworthy enough to answer from.
    "data_unavailable",
    "context_limit",
    "retrieval_context_limit",
    "turn_cost_limit",
    "model_call_limit",
    "request_cancelled",
    # The spending ledger can't be reached, or a person paused spending to take a look.
    "accounting_unavailable",
    "accounting_paused",
    # A bug in the Brain. The turn log has the details.
    "internal_error",
]

# Worth the student pressing Try again right away.
RETRYABLE: frozenset[str] = frozenset({
    "busy", "rate_limited", "model_timeout", "model_unreachable", "model_provider_error",
    "invalid_model_output", "data_unavailable",
})


class ErrorBody(StrictModel):
    code: ErrorCode
    message: str = Field(min_length=1)
    retryable: bool
    resetAt: str | None = None  # When a spent allowance comes back.
    resources: list[Source] | None = None
    # Every failure carries it, except a cancelled request nobody is waiting for.
    emergency: EmergencyHelp | None = None


class FailureReply(StrictModel):
    """Any non-200 status: no answer, but what went wrong and where to get help."""

    error: ErrorBody
    reason: ErrorCode
    requestId: str
    # Development only, as on ChatReply.
    metrics: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None


# ---- Streaming: the same turn with Accept: text/event-stream ----

Stage = Literal["connecting", "understanding", "retrieving", "calculating", "composing",
                "reviewing"]


class Subject(StrictModel):
    """What a step is about, shown as "Looking up dinner menu items (Sep 29, 2026)"."""

    topic: str
    meal: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class SafetyBlock(StrictModel):
    """The code-written emergency block, sent the moment danger is read."""

    answer: str = Field(min_length=1, max_length=4_000)
    citations: list[Citation] = []


class ProgressEvent(StrictModel):
    """`event: progress`. Public step names only, never the model's reasoning."""

    stage: Stage
    subjects: list[Subject] = []
    operation: str | None = None
    # A draft still being checked, shown only while stage is "reviewing".
    draft: str | None = None
    safety: SafetyBlock | None = None


class ResultEvent(StrictModel):
    """`event: result`, always last: the HTTP status and body a plain request would get."""

    status: int = Field(ge=200, le=599)
    body: ChatReply | FailureReply
