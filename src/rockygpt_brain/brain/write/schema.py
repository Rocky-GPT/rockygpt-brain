"""What BRAIN #3 returns: the prose, and what to offer asking next."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    answer: str
    suggested_questions: list[str] = Field(default_factory=list, alias="suggestedQuestions")
