from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    sufficient_evidence: bool = Field(alias="sufficientEvidence")
    answer: str
    suggested_questions: list[str] = Field(default_factory=list, alias="suggestedQuestions")
