"""The JSON schema `submit_answer` is advertised to the model with.

A data table, kept beside — but not inside — the validator that enforces it.
Every bound here is imported from `brain/answer.py` rather than restated, so
the contract the model is shown and the contract enforced on its output
cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from rockygpt_brain.brain.answer import (
    MAX_ANSWER_LENGTH,
    MAX_CITED_SOURCE_ID_LENGTH,
    MAX_CITED_SOURCE_IDS,
    MAX_SUGGESTED_QUESTION_LENGTH,
    MAX_SUGGESTED_QUESTIONS,
    MAX_UI_ACTIONS,
    SUBMIT_ANSWER_TOOL_NAME,
)
from rockygpt_brain.schemas.common import (
    MAX_UI_ACTION_PAYLOAD_ENTRIES,
    MAX_UI_ACTION_PAYLOAD_KEY_LENGTH,
    MAX_UI_ACTION_PAYLOAD_VALUE_LENGTH,
)

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
                "route": {
                    "type": "string",
                    "enum": ["standard", "ungrounded", "conversation"],
                    "description": (
                        "'standard' means every campus fact in this answer is "
                        "backed by a citedSourceId from this turn. "
                        "'conversation' means the answer reports what you said "
                        "earlier in this conversation, taken from the record of "
                        "it — use this for 'what did you tell me', 'what time "
                        "did you say', and similar, and cite nothing. Use "
                        "'ungrounded' for everything else — small talk, general "
                        "knowledge, questions about you, and any answer you have "
                        "nothing to cite for."
                    ),
                },
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
                    "description": (
                        "Campus panels to open beside the answer. VIEW_MAP takes "
                        "payload {\"locationKey\": <a `key` from a search_map result "
                        "this turn} and belongs on any 'where is X' answer. VIEW_MENU "
                        "takes optional {\"meal\": <breakfast|lunch|dinner>}. VIEW_BUS, "
                        "VIEW_EVENTS, VIEW_PRINT, and VIEW_DIRECTORY take no payload."
                    ),
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
