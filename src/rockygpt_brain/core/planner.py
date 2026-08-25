"""AI #1: the question, translated into operations.

    question in -> one model call -> a plan out

In goes the question, what has already been said, the time, and the list of
what Rocky can do. Out comes a plan written in the vocabulary of `plan.py`.
Nothing here decides whether the plan is any good; `validate.check` does that.

The instruction below describes lanes, fields and operations, and it contains
no question, phrase, or worked example. It must not gain one. The moment a
question shape appears here, the translator has become a list of intents, and
the next question needs a code change again.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from openai import AsyncOpenAI

from rockygpt_brain.core.capabilities import catalogue
from rockygpt_brain.core.plan import TIME_WORDS, Plan
from rockygpt_brain.errors import ServiceError

_PLAN = """Translate the question into a plan. Choose one lane.

CODE     the answer is a lookup in campus data. Name the capability.
RAG      the answer is written in a campus document. Give the topic.
GENERAL  the answer is general knowledge.
SAFETY   the person may be at risk of harm.
MEMORY   the question is about what was already said. Give the query.

`capabilities` is everything Rocky can look up, and the fields each one allows.
For CODE, name one capability and use only its fields.

Narrow the rows with `filters`, drawn from that capability's filter fields.
Then say what to do with the rows that are left: `orderBy` one of its fields
with a `direction`, a `limit`, `count` to answer with how many there are,
`compare` to report fields side by side. Name only fields the capability lists.

A filter value may be one of `timeWords` in place of a date or a time. Python
resolves it against `currentTime`. Do not work out any date yourself.

`earlierTurns` is what has already been said in this conversation. Use it only
to work out what a follow-up refers to."""


class PlannerPort(Protocol):
    configured: bool

    async def plan(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Plan: ...


class OpenAIPlanner:
    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self.configured = bool(api_key) or client is not None
        self._model = model
        self._client = client or (AsyncOpenAI(api_key=api_key) if api_key else None)

    async def plan(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Plan:
        if self._client is None:
            raise ServiceError(
                503, "SERVICE_UNAVAILABLE", "OPENAI_API_KEY is not configured.", retryable=True
            )
        payload = {
            "question": question,
            "earlierTurns": context,
            "currentTime": current_time,
            "capabilities": catalogue(),
            "timeWords": list(TIME_WORDS),
        }
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=_PLAN,
                input=json.dumps(payload, default=str),
                text_format=Plan,
                store=False,
            )
            if response.output_parsed is None:
                raise ValueError("empty structured response")
            return Plan.model_validate(response.output_parsed)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "The planning service is temporarily unavailable.",
                retryable=True,
            ) from exc
