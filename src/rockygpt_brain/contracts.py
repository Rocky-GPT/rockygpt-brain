"""The conversation and model-output boundary; no intent labels or hidden state."""

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
    evidence_ids: list[str] = Field(max_length=12)


class Answer(StrictModel):
    status: Literal["answered", "partial", "clarification", "unavailable"]
    parts: list[AnswerPart] = Field(min_length=1, max_length=12)


class EvidenceUse(StrictModel):
    evidence_id: str
    assertion_subject: Literal["record_subject", "referenced_entity"] = Field(
        description=(
            "Does the assertion describe the record's own subject, or an entity merely "
            "mentioned in it? Use the server-supplied evidence_subjects. Classify the "
            "subject of the assertion, not just the noun in a matching quote."
        )
    )


class PartReview(StrictModel):
    part_index: int = Field(ge=0, le=11)
    verdict: Literal[
        "supported", "unsupported_claim", "contradicted_evidence", "wrong_scope", "wrong_context"
    ]
    reason: str = Field(max_length=400)
    evidence_uses: list[EvidenceUse] = Field(
        max_length=24,
        description=(
            "Classify every cited record's use, even for a supported part or a part labelled "
            "guidance/limitation. Include both uses if the paragraph makes assertions about "
            "both the record subject and a referenced entity. No cited ID may be omitted."
        ),
    )


class EvidenceReview(StrictModel):
    parts: list[PartReview] = Field(min_length=1, max_length=12)
