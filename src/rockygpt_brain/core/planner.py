"""BRAIN #1: the question, translated into operations.

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
from typing import Any, Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from rockygpt_brain.capabilities.registry import catalogue
from rockygpt_brain.core.plan import TIME_WORDS, Plan, Understanding
from rockygpt_brain.errors import ServiceError

_T = TypeVar("_T", bound=BaseModel)

_UNDERSTAND = """Work out what the question is asking, in four steps, in this order.

`normalized`  the question with its wording tidied — spelling, spacing,
              punctuation — and nothing else. Follow nothing, fill in nothing.
`references`  everything the question borrows from `earlierTurns`, each with
              what it stands for. Some are a word standing in for a thing —
              "it", "that one", "there". Some are a subject left out
              altogether: "what about tomorrow" names a day and no topic, so
              the topic is borrowed; put the words that carry the gap in
              `text`. Nothing that needs no conversation belongs here — not
              "you", not "Rocky", not a date the clock already gives.
`usedTurns`   the positions in `earlierTurns` those references point into,
              counting from 0. Empty when there are no references.
`usesContext` true when this question needs the conversation — because it
              borrows from it, or because it is about what was said. False for
              a question that would mean the same asked first.
`resolved`    the question rewritten to stand on its own, with what it pointed
              at written in. It is still a question and still the one asked —
              do not answer it, and do not replace it with what was said
              before. A question that points nowhere resolves to `normalized`.

Whoever reads `resolved` next will not see this conversation, or the words as
typed. Everything the question needs has to be in it."""

_PLAN = """Say what to do about the question.

`safety` lists what is wrong with the question, and is empty when nothing is:
`emergency` someone may be harmed now, `privacy` it asks for someone else's
personal information, `secret` it asks for credentials or how Rocky is built,
`harmful` answering as asked would cause harm. Judge the question, not the
subject it raises. List every one that applies, then choose a lane anyway.

Choose one lane.

CODE     the answer is a lookup in campus data. Name the capability.
RAG      the answer is written in a campus document. Give the topic.
GENERAL  the answer is general knowledge. Say which kind with `freshness`:
         `stable` if the answer is the same whenever it is asked, and
         `current` if an honest answer would have to say "as of" some date —
         anything measured, counted, priced, ranked, or currently held, however
         slowly it moves. For `current`, give the `query` to look up: what it
         means, in words. Leave the date out — Python adds it.

`capabilities` is everything Rocky can look up, and the fields each one allows.
For CODE, name one capability and use only its fields.

Narrow the rows with `filters`, drawn from that capability's filter fields.
Then say what to do with the rows that are left: `orderBy` one of its fields
with a `direction`, a `limit`, `count` to answer with how many there are,
`compare` to report fields side by side. Name only fields the capability lists.

A filter value may be one of `timeWords` in place of a date or a time. Python
resolves it against `currentTime`. Do not work out any date yourself.

The question has already been read and written out in full. There is no
conversation to consult: what you are given is all there is."""


class PlannerPort(Protocol):
    configured: bool

    async def understand(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Understanding: ...

    async def plan(self, resolved: str, current_time: str) -> Plan: ...


class OpenAIPlanner:
    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self.configured = bool(api_key) or client is not None
        self._model = model
        self._client = client or (AsyncOpenAI(api_key=api_key) if api_key else None)

    async def understand(
        self,
        question: str,
        context: list[dict[str, Any]],
        current_time: str,
    ) -> Understanding:
        return await self._call(
            _UNDERSTAND,
            {"question": question, "earlierTurns": context, "currentTime": current_time},
            Understanding,
        )

    async def plan(self, resolved: str, current_time: str) -> Plan:
        """The question, and nothing else that was said.

        `earlierTurns` is deliberately absent. A plan built from a resolved
        question that still needed the conversation would be a plan built on a
        resolution that failed, and this is where that shows.
        """
        return await self._call(
            _PLAN,
            {
                "question": resolved,
                "currentTime": current_time,
                "capabilities": catalogue(),
                "timeWords": list(TIME_WORDS),
            },
            Plan,
        )

    async def _call(self, instructions: str, payload: dict[str, Any], shape: type[_T]) -> _T:
        if self._client is None:
            raise ServiceError(
                503, "SERVICE_UNAVAILABLE", "OPENAI_API_KEY is not configured.", retryable=True
            )
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=instructions,
                input=json.dumps(payload, default=str),
                text_format=shape,
                store=False,
            )
            if response.output_parsed is None:
                raise ValueError("empty structured response")
            return shape.model_validate(response.output_parsed)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "The planning service is temporarily unavailable.",
                retryable=True,
            ) from exc
