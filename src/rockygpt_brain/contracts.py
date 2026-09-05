"""The conversation and model-output boundary; no intent labels or hidden state."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16000)

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message content must not be blank")
        return value


class ChatRequest(StrictModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def complete_conversation(self) -> "ChatRequest":
        if self.messages[0].role != "user" or self.messages[-1].role != "user":
            raise ValueError("Conversation must begin and end with a user message")
        if sum(len(m.content) for m in self.messages) > 48000:
            raise ValueError("Conversation is too long; start a new conversation")
        return self


class AnswerPart(StrictModel):
    kind: Literal["campus_fact", "guidance", "limitation", "clarification"]
    text: str = Field(min_length=1, max_length=6000)
    evidence_ids: list[str] = Field(max_length=50)


class Answer(StrictModel):
    status: Literal["answered", "partial", "clarification", "unavailable"]
    parts: list[AnswerPart] = Field(min_length=1, max_length=12)


class PartReview(StrictModel):
    part_index: int = Field(ge=0, le=11)
    verdict: Literal[
        "supported", "unsupported_claim", "contradicted_evidence", "wrong_scope", "wrong_context"
    ]
    reason: str = Field(max_length=400)
    uses_event_for_entity: bool = Field(
        description=(
            "True if ANY assertion in this paragraph uses its server-listed event citations "
            "to infer general attributes of a referenced facility or organization, even "
            "when mixed with actual event facts. False for an event's own details or an "
            "honest statement that an event does not verify an entity attribute. False if "
            "this paragraph's event_citations list is empty. Evaluate every paragraph kind."
        ),
    )
    infers_food_safety: bool = Field(
        description=(
            "True if this part uses menu, dietary, or allergen labels (including blank or "
            "missing labels) to infer that food is safe, safer, or lower risk for an allergy. "
            "A later caveat does not undo this inference. False when the part only reports "
            "published labels or recommends asking dining staff about ingredients and "
            "cross-contact without ranking food safety."
        )
    )
    plan_deadlines: list[datetime] = Field(
        max_length=12,
        description=(
            "For EVERY explicit schedule/deadline constraint on an action this paragraph "
            "recommends taking, extract its latest usable ISO timestamp with timezone. "
            "For a proposed fixed start or 'before' time, use that time; for joining an "
            "ongoing event or service window, use its end. Include these timestamps even "
            "when the verdict is supported and even if they are already past. Use the "
            "supplied campus date for today/tonight. Do not include merely quoted historical "
            "schedules or hypothetical examples that are not recommendations for this student. "
            "Advice to act now or as soon as possible has no expiry by itself. Use an empty "
            "list when the proposed actions have no explicit time bounds."
        ),
    )


class EvidenceReview(StrictModel):
    parts: list[PartReview] = Field(min_length=1, max_length=12)
