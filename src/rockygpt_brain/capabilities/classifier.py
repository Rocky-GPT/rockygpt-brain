"""Single-model classifier for RockyGPT capability ownership."""

from collections.abc import Sequence
from importlib.resources import files
from typing import Literal, TypedDict, cast

from openai import OpenAI
from openai.types.responses import (
    FunctionToolParam,
    ResponseInputParam,
    ToolChoiceFunctionParam,
)
from pydantic import BaseModel, ConfigDict, ValidationError

CapabilityLabel = Literal[
    "transportation",
    "dining",
    "events",
    "hours",
    "directory",
    "locations",
    "courses",
    "programs",
    "clubs",
    "academic_calendar",
    "campus_documents",
    "student_services",
    "it_support",
    "personal_account",
    "general",
    "clarification",
]

CAPABILITY_LABELS: tuple[CapabilityLabel, ...] = (
    "transportation",
    "dining",
    "events",
    "hours",
    "directory",
    "locations",
    "courses",
    "programs",
    "clubs",
    "academic_calendar",
    "campus_documents",
    "student_services",
    "it_support",
    "personal_account",
    "general",
    "clarification",
)

CLASSIFIER_TOOL_NAME = "select_capability"
CLASSIFIER_INSTRUCTIONS = (
    files("rockygpt_brain.capabilities")
    .joinpath("prompt.md")
    .read_text(encoding="utf-8")
    .strip()
)


class ConversationMessage(TypedDict):
    """One original ordered conversation message."""

    role: Literal["user", "assistant"]
    content: str


class CapabilitySelection(BaseModel):
    """The only model-generated value accepted from the classifier."""

    model_config = ConfigDict(extra="forbid")

    capability: CapabilityLabel


CLASSIFIER_TOOL = cast(
    FunctionToolParam,
    {
        "type": "function",
        "name": CLASSIFIER_TOOL_NAME,
        "description": "Select the one RockyGPT capability that owns the latest request.",
        "parameters": {
            "type": "object",
            "properties": {
                "capability": {
                    "type": "string",
                    "enum": list(CAPABILITY_LABELS),
                }
            },
            "required": ["capability"],
            "additionalProperties": False,
        },
        "strict": True,
    },
)


def classify(
    messages: Sequence[ConversationMessage], model: str
) -> tuple[CapabilityLabel, str]:
    """Return one bounded capability label while preserving message order."""
    response = OpenAI().responses.create(
        model=model,
        input=cast(ResponseInputParam, list(messages)),
        instructions=CLASSIFIER_INSTRUCTIONS,
        tools=[CLASSIFIER_TOOL],
        tool_choice=cast(
            ToolChoiceFunctionParam,
            {"type": "function", "name": CLASSIFIER_TOOL_NAME},
        ),
        parallel_tool_calls=False,
        store=False,
        temperature=0,
    )
    calls = [
        item
        for item in response.output
        if item.type == "function_call" and item.name == CLASSIFIER_TOOL_NAME
    ]
    if len(calls) != 1:
        return "clarification", response.model
    try:
        selection = CapabilitySelection.model_validate_json(calls[0].arguments)
    except (ValidationError, ValueError):
        return "clarification", response.model
    return selection.capability, response.model
