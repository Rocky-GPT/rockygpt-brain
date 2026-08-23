"""POST /v1/feedback request/response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from rockygpt_brain.schemas.common import IDENTIFIER_PATTERN, StrictModel

FeedbackCategory = Literal["inaccurate", "incomplete", "could_be_better", "outdated", "other"]


class FeedbackRequest(StrictModel):
    request_id: str = Field(
        min_length=1, max_length=128, pattern=IDENTIFIER_PATTERN, alias="requestId"
    )
    rating: Literal[-1, 1]
    category: FeedbackCategory | None = None
    comments: str | None = Field(default=None, max_length=2000)


class FeedbackSuccess(StrictModel):
    success: Literal[True]
