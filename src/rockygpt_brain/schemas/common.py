"""Shared schema pieces: components.schemas in spec/brain-api.openapi.yaml."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
IdentifierStr = str

UiActionType = Literal[
    "VIEW_MENU",
    "VIEW_BUS",
    "VIEW_PRINT",
    "VIEW_EVENTS",
    "VIEW_MAP",
    "VIEW_DIRECTORY",
]

QuestionOrigin = Literal["client", "dev", "bot"]


class StrictModel(BaseModel):
    """Base for every wire schema: additionalProperties: false everywhere."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ChatTurn(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class Citation(StrictModel):
    source_id: str | None = Field(default=None, max_length=256, alias="sourceId")
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(max_length=2048)
    source_path: str | None = Field(default=None, max_length=1024, alias="sourcePath")
    snippet: str | None = Field(default=None, max_length=1000)
    collected_at: datetime | None = Field(default=None, alias="collectedAt")


MAX_UI_ACTION_PAYLOAD_ENTRIES = 5
MAX_UI_ACTION_PAYLOAD_KEY_LENGTH = 64
MAX_UI_ACTION_PAYLOAD_VALUE_LENGTH = 500

_CONTROL_OR_FORMAT_CATEGORIES = ("Cc", "Cf", "Cs", "Co")


def _has_control_or_format_chars(text: str) -> bool:
    return any(unicodedata.category(ch) in _CONTROL_OR_FORMAT_CATEGORIES for ch in text)


class UiAction(StrictModel):
    type: UiActionType
    payload: dict[str, str] | None = None

    @field_validator("payload")
    @classmethod
    def _bound_payload(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        # The OpenAPI contract (`payload: object, additionalProperties:
        # string`) doesn't specify entry/length/character caps, so bounding
        # here is compliant, not a deviation — and load-bearing for
        # UiAction's other use as a validation target for untrusted model
        # tool-call output (see brain/answer.py), not just as a
        # response-serialization shape.
        if value is None:
            return None
        if len(value) > MAX_UI_ACTION_PAYLOAD_ENTRIES:
            raise ValueError("payload has too many entries")
        for key, entry_value in value.items():
            # Keys are identity-bearing (they select which UI field a
            # value fills, e.g. "meal"/"locationKey"): whitespace-only is
            # treated as empty, and surrounding whitespace is rejected
            # rather than silently trimmed, so two distinct keys can never
            # be folded together.
            if not key.strip() or key != key.strip():
                raise ValueError("payload key is empty or has surrounding whitespace")
            if len(key) > MAX_UI_ACTION_PAYLOAD_KEY_LENGTH:
                raise ValueError("payload key is too long")
            if _has_control_or_format_chars(key):
                raise ValueError("payload key contains a control/format character")
            # Values are ordinary display/query text, not identifiers, so
            # surrounding whitespace is fine — only control/format
            # characters (which could create unsafe or ambiguous UI
            # behavior, e.g. bidi overrides) are rejected.
            if len(entry_value) > MAX_UI_ACTION_PAYLOAD_VALUE_LENGTH:
                raise ValueError("payload value is too long")
            if _has_control_or_format_chars(entry_value):
                raise ValueError("payload value contains a control/format character")
        return value


class Health(BaseModel):
    """additionalProperties: true in the spec, so this stays permissive."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    status: Literal["healthy", "ok"]
    service: str | None = None
    uptime_seconds: float | None = Field(default=None, ge=0, alias="uptimeSeconds")


class Readiness(StrictModel):
    status: Literal["ready", "unready"]
    failing: list[str] | None = None
    timestamp: datetime | None = None


class ErrorDetail(StrictModel):
    code: Literal[
        "INVALID_REQUEST",
        "PAYLOAD_TOO_LARGE",
        "UNAUTHORIZED",
        "RATE_LIMITED",
        "DATASET_UNAVAILABLE",
        "SERVICE_UNAVAILABLE",
        "INTERNAL_ERROR",
        "NOT_FOUND",
    ]
    message: str = Field(min_length=1, max_length=500)
    retryable: bool
    retry_after_seconds: int | None = Field(default=None, ge=1, alias="retryAfterSeconds")


class ErrorResponse(StrictModel):
    request_id: str = Field(alias="requestId", min_length=1, max_length=128)
    error: ErrorDetail
