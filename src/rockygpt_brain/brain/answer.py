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
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from rockygpt_brain.schemas.common import (
    UiAction,
)

MAX_ANSWER_LENGTH = 8_000
MAX_CITED_SOURCE_IDS = 32
MAX_CITED_SOURCE_ID_LENGTH = 256
MAX_UI_ACTIONS = 10
MAX_SUGGESTED_QUESTIONS = 10
MAX_SUGGESTED_QUESTION_LENGTH = 120

_CONTROL_OR_FORMAT_CATEGORIES = ("Cc", "Cf", "Cs", "Co")

# "conversation" is a claim about what was said earlier in this conversation,
# not about the campus. It carries no citedSourceIds for the same reason
# "ungrounded" does not — there is no campus source for a fact about a
# conversation — but it is a *verified* answer drawn from the discourse record,
# not an admission that nothing could be verified. Keeping them apart is what
# stops "what did you tell me" from being refused as an unsourceable campus
# claim (brain/discourse.py).
ModelRoute = Literal["standard", "ungrounded", "conversation"]

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

    # There is deliberately no validator rejecting a route/citation mismatch.
    #
    # Both directions of that mismatch are a *mislabeled field* on an answer
    # whose text is usually fine, and rejecting a submission costs the whole
    # turn — the orchestrator falls back to a canned apology. Measured once
    # route "conversation" came into use: one such rejection per ~180 turns
    # discarded an otherwise-correct answer, surfaced by the fallback reason
    # `submit_malformed:unknown:value_error` (rockygpt-evals/corpus).
    #
    # `finalize.finalize` normalises both directions instead, so the invariant
    # still holds at the API boundary:
    #   route "standard" with no citations             -> downgraded to "ungrounded"
    #   route "ungrounded"/"conversation" + citations   -> citations dropped
    #
    # Normalisation only ever runs in the conservative direction. A route
    # claiming nothing was verified is never promoted to one claiming it was.


def parse_submit_answer(arguments_json: str) -> SubmitAnswerArguments | None:
    parsed, _ = parse_submit_answer_diagnosed(arguments_json)
    return parsed


def parse_submit_answer_diagnosed(
    arguments_json: str,
) -> tuple[SubmitAnswerArguments | None, str | None]:
    """Parse, and say which rule rejected it when one does.

    The diagnostic is built from the *schema* — the field path and Pydantic's
    error type — never from the submitted values. A rejected submission costs
    the whole turn, so knowing which rule fired is the difference between
    fixing a real defect and guessing at one; retaining what the model wrote
    would defeat the redaction the rest of this service is built around.
    """
    try:
        data = json.loads(arguments_json)
    except (ValueError, UnicodeDecodeError):
        return None, "not_json"
    if not isinstance(data, dict):
        return None, "not_object"
    try:
        return SubmitAnswerArguments.model_validate(data), None
    except ValidationError as error:
        details = error.errors()
        if not details:
            return None, "unknown:invalid"
        first = details[0]
        location = ".".join(str(part) for part in first["loc"]) or "unknown"
        return None, f"{location}:{first['type']}"
