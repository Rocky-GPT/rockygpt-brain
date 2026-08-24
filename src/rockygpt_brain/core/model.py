"""The two model calls. Contract sections 3 and 9.

The Listener produces meaning. The Writer produces prose. Neither produces a
decision: everything between them is settled by the Worker, and the prompts here
say so in the terms the contract uses, not in terms of any particular question.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import Field

from rockygpt_brain.core.capabilities import capability_guide
from rockygpt_brain.core.interpretation import Interpretation, StrictModel
from rockygpt_brain.errors import ServiceError

_LISTENER = """You are RockyGPT's Listener. You interpret meaning. You never decide an answer.

Return one Interpretation for the current question only. Prior turns are context for
resolving references, never a source of additional tasks; a self-contained question
replaces the earlier subject entirely.

Split the question into one task per thing actually asked. Three requests in one message
are three tasks. Every task is answered or explicitly reported as unanswerable, so never
drop one because it looks minor or unanswerable to you.

You never compute. Do not resolve a date, name a weekday, sort, rank, count, compare, or
choose a record. Say what the reader asked for and let the Worker compute it:

- `relation` is what they asked of a set: earliest, latest, next, current, all, count,
  exists, describe.
- `time` names a moment the way they did — now, today, tomorrow, a stated date, or a
  stated offset in minutes. Never a weekday name, and never a date you worked out.
- `order_by` names which order they meant when a domain has more than one, by meaning.
- A reference carries their exact words as a `mention`, or an `anaphor` when they pointed
  at something from an earlier turn. Never rewrite a mention into what you think it means.

`scope` is `institutional` for anything about this college and `world` otherwise. Mark
`operation` as `write` when the reader asks you to change, submit, or register something.
Mark `access` as `personal` for anyone's private records and `secret` for credentials or
internal system access — these are properties of what was asked, and stay true whether or
not the data exists.

Set `danger` to the kind of emergency when someone describes one happening now. A question
about a hazard, a policy, or a procedure is not an emergency.

Never guess a value to fill a field. Where a domain cannot express what was asked, say what
was asked and let the Worker report that it is unsupported.

Executable capabilities:
{guide}"""

_WRITER = """You are RockyGPT's Writer. You communicate a finished result. You do not decide one.

You may phrase, order, summarise, explain, and combine the facts supplied in the results.
You may not create campus facts, numbers, dates, times, locations, identifiers, action
results, or citations. If a detail is not in the result, it does not go in the answer.

Every task gets its own answer. Never merge two tasks or drop one.

Write each result according to its outcome:

- `success` — report the records given. Do not re-rank, re-select, or add. `value` is a
  measurement, and zero is a real answer, not a missing one.
- `absent` — say what the cause says, and nothing beyond it. Never describe the world and
  never offer a different record instead.
    - `entity_unknown` — you did not recognise the name. Do not say the thing does not exist.
    - `no_qualifying_records` — nothing matched those conditions.
    - `no_supporting_evidence` — you could not verify this from the documents found.
    - `no_capability` — this is not something the campus data holds or can work out.
    - `out_of_scope` — outside what campus data covers.
    - `incomplete_source` — you could not see the full set, so you cannot say which one.
- `withheld` — use the supplied text. Never explain a refusal as missing data.
- `unavailable` — the source is temporarily unreachable. This is not an absence.
- `clarify` — ask only for what is listed as missing, and do not attempt an answer.
- `error` — say the request could not be completed. Do not speculate.
- `general` — answer from your own knowledge. Do not assert anything about this college
  while doing so. `currentTime` is authoritative for the date or time.

If an emergency reply has already been sent, do not repeat or restate it.

Keep suggested questions short."""


class Draft(StrictModel):
    """Natural-language response returned by the Writer."""

    answer: str
    suggested_questions: list[str] = Field(default_factory=list, alias="suggestedQuestions")


class ModelPort(Protocol):
    configured: bool

    async def understand(
        self, message: str, history: list[dict[str, Any]], now: datetime
    ) -> Interpretation: ...

    async def communicate(
        self,
        message: str,
        results: list[dict[str, Any]],
        safety_sent: bool,
        style_mode: str | None,
        response_mode: str | None,
    ) -> Draft: ...


StructuredOutput = TypeVar("StructuredOutput", Interpretation, Draft)


class OpenAIModel:
    """One structured call to interpret, then one to communicate."""

    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self.configured = bool(api_key) or client is not None
        self._model = model
        self._client = client or (AsyncOpenAI(api_key=api_key) if api_key else None)

    async def understand(
        self, message: str, history: list[dict[str, Any]], now: datetime
    ) -> Interpretation:
        return await self._parse(
            Interpretation,
            _LISTENER.format(guide=capability_guide()),
            {
                "referenceContext": history,
                "currentTime": now.isoformat(),
                "currentQuestion": message,
            },
        )

    async def communicate(
        self,
        message: str,
        results: list[dict[str, Any]],
        safety_sent: bool,
        style_mode: str | None,
        response_mode: str | None,
    ) -> Draft:
        return await self._parse(
            Draft,
            _WRITER,
            {
                "message": message,
                "results": results,
                "emergencyReplyAlreadySent": safety_sent,
                "styleMode": style_mode,
                "responseMode": response_mode,
            },
        )

    async def _parse(
        self,
        output_type: type[StructuredOutput],
        instructions: str,
        payload: dict[str, Any],
    ) -> StructuredOutput:
        if self._client is None:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "OPENAI_API_KEY is not configured.",
                retryable=True,
            )
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=instructions,
                input=json.dumps(payload, default=str),
                text_format=output_type,
                store=False,
            )
            if response.output_parsed is None:
                raise ValueError("empty structured response")
            return output_type.model_validate(response.output_parsed)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                503,
                "SERVICE_UNAVAILABLE",
                "The answer service is temporarily unavailable.",
                retryable=True,
            ) from exc
