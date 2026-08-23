"""The model's final-answer tool.

`submit_answer` is the only way the tool-calling loop in orchestrator.py
ends: its structured arguments are the sole source of the answer text,
route, cited source ids, UI actions, and suggested questions for a turn —
but, like every other model output in this codebase, they are re-validated
strictly server-side rather than trusted because they arrived via a
JSON-schema-constrained tool call.

Policy is deterministic and uniform: **whole-answer rejection**, not
per-item filtering/clipping. If any part of the payload is invalid — a
blank answer, an oversized or malformed field, a duplicate or
whitespace/control-character-containing `citedSourceIds`/
`suggestedQuestions` entry, an invalid `uiActions` entry, or
`route: "ungrounded"` paired with any `citedSourceIds` — `parse_submit_answer`
returns `None` for the whole payload, and the caller falls back to a safe
default answer. This is simpler to reason about and test than partial
filtering, and it means an inconsistent answer (e.g. one that cites a
source while claiming it can't verify anything) is never silently
"cleaned up" into something that looks consistent but wasn't what the
model actually said.

The model is `strict=True`: no implicit type coercion. Per-item length
bounds are enforced structurally by Pydantic via `Annotated`/
`StringConstraints` item types (so Pydantic itself rejects an oversized
item, never truncates one); whitespace/control-character/duplicate checks
that Pydantic's built-in constraints can't express run as field validators
that also raise (never filter) on the *raw* submitted list — including the
`route`/`citedSourceIds` invariant, which is checked against the raw list
so an ungrounded answer cannot evade it by supplying only citations that
would otherwise have been dropped.

`citedSourceIds` entries are identity-bearing lookup keys into
`brain.grounding.ProvenanceRegistry` (see that module for why the model
cannot use one to fabricate a citation), so they must match exactly:
surrounding whitespace or a control/format character invalidates the whole
answer rather than being silently trimmed.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from rockygpt_brain.schemas.common import (
    MAX_UI_ACTION_PAYLOAD_ENTRIES,
    MAX_UI_ACTION_PAYLOAD_KEY_LENGTH,
    MAX_UI_ACTION_PAYLOAD_VALUE_LENGTH,
    UiAction,
)

MAX_ANSWER_LENGTH = 8_000
MAX_CITED_SOURCE_IDS = 32
MAX_CITED_SOURCE_ID_LENGTH = 256
MAX_UI_ACTIONS = 10
MAX_SUGGESTED_QUESTIONS = 10
MAX_SUGGESTED_QUESTION_LENGTH = 120

_CONTROL_OR_FORMAT_CATEGORIES = ("Cc", "Cf", "Cs", "Co")

ModelRoute = Literal["standard", "ungrounded"]

SUBMIT_ANSWER_TOOL_NAME = "submit_answer"

CitedSourceId = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_CITED_SOURCE_ID_LENGTH)
]
SuggestedQuestion = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_SUGGESTED_QUESTION_LENGTH)
]


def _has_control_or_format_chars(text: str) -> bool:
    return any(unicodedata.category(ch) in _CONTROL_OR_FORMAT_CATEGORIES for ch in text)


class SubmitAnswerArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer_markdown: str = Field(
        min_length=1, max_length=MAX_ANSWER_LENGTH, alias="answerMarkdown"
    )
    route: ModelRoute
    cited_source_ids: list[CitedSourceId] = Field(
        default_factory=list,
        max_length=MAX_CITED_SOURCE_IDS,
        alias="citedSourceIds",
    )
    ui_actions: list[UiAction] = Field(
        default_factory=list, max_length=MAX_UI_ACTIONS, alias="uiActions"
    )
    suggested_questions: list[SuggestedQuestion] = Field(
        default_factory=list,
        max_length=MAX_SUGGESTED_QUESTIONS,
        alias="suggestedQuestions",
    )

    @field_validator("answer_markdown")
    @classmethod
    def _require_non_blank_answer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answerMarkdown must not be blank")
        return value

    @field_validator("cited_source_ids")
    @classmethod
    def _validate_cited_source_ids(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("citedSourceIds must not contain duplicates")
        for value in values:
            if value != value.strip():
                raise ValueError("citedSourceIds must not have surrounding whitespace")
            if _has_control_or_format_chars(value):
                raise ValueError("citedSourceIds must not contain control/format characters")
        return values

    @field_validator("suggested_questions")
    @classmethod
    def _validate_suggested_questions(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("suggestedQuestions must not contain duplicates")
        for value in values:
            if not value.strip():
                raise ValueError("suggestedQuestions must not be blank")
            if _has_control_or_format_chars(value):
                raise ValueError(
                    "suggestedQuestions must not contain control/format characters"
                )
        return values

    @model_validator(mode="after")
    def _ungrounded_carries_no_citations(self) -> Self:
        # A citation asserts a supported claim; route "ungrounded" means "I
        # could not verify this." The two are contradictory. This runs
        # against `self.cited_source_ids` after field validation, which for
        # this field only ever raises or passes the list through unchanged
        # (never filters), so an ungrounded answer cannot evade this check
        # by supplying citations that would otherwise have been dropped.
        if self.route == "ungrounded" and self.cited_source_ids:
            raise ValueError("route 'ungrounded' must not include citedSourceIds")
        return self


SUBMIT_ANSWER_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": SUBMIT_ANSWER_TOOL_NAME,
        "description": (
            "Submit the final answer for this turn. Call this exactly once, "
            "as the last step. route 'ungrounded' must not include "
            "citedSourceIds. citedSourceIds/suggestedQuestions must not "
            "contain duplicates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "answerMarkdown": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_ANSWER_LENGTH,
                },
                "route": {"type": "string", "enum": ["standard", "ungrounded"]},
                "citedSourceIds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_CITED_SOURCE_ID_LENGTH,
                    },
                    "maxItems": MAX_CITED_SOURCE_IDS,
                    "uniqueItems": True,
                    "description": "Exact sourceId values from this turn's tool results only.",
                },
                "uiActions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": [
                                    "VIEW_MENU",
                                    "VIEW_BUS",
                                    "VIEW_PRINT",
                                    "VIEW_EVENTS",
                                    "VIEW_MAP",
                                    "VIEW_DIRECTORY",
                                ],
                            },
                            "payload": {
                                "type": "object",
                                "maxProperties": MAX_UI_ACTION_PAYLOAD_ENTRIES,
                                "propertyNames": {
                                    "minLength": 1,
                                    "maxLength": MAX_UI_ACTION_PAYLOAD_KEY_LENGTH,
                                },
                                "additionalProperties": {
                                    "type": "string",
                                    "maxLength": MAX_UI_ACTION_PAYLOAD_VALUE_LENGTH,
                                },
                            },
                        },
                        "required": ["type"],
                        "additionalProperties": False,
                    },
                    "maxItems": MAX_UI_ACTIONS,
                },
                "suggestedQuestions": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_SUGGESTED_QUESTION_LENGTH,
                    },
                    "maxItems": MAX_SUGGESTED_QUESTIONS,
                    "uniqueItems": True,
                },
            },
            "required": ["answerMarkdown", "route"],
            "additionalProperties": False,
        },
    },
}


def parse_submit_answer(arguments_json: str) -> SubmitAnswerArguments | None:
    try:
        data = json.loads(arguments_json)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return SubmitAnswerArguments.model_validate(data)
    except ValidationError:
        return None
