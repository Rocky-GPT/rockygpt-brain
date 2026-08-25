"""The one way this brain talks to a model.

Three stages call a model — understand, plan, write — and each was carrying
its own copy of the same twelve lines: build the client, post the payload,
unwrap the parse, turn any failure into a `ServiceError`. Three copies meant
three chances for them to drift, and a retry policy that had to be fixed in
three places.

The instructions and the schema are the caller's. Everything else is here.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from rockygpt_brain.errors import ServiceError

_T = TypeVar("_T", bound=BaseModel)


class StructuredModel:
    """A model that answers in a given shape, or fails the turn.

    `unavailable` is the message a caller wants a person to see when this does
    not work. It belongs to the stage, not to the transport: told "the planning
    service is unavailable" for a failed answer call, whoever reads the log
    goes looking in the wrong place.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str,
        unavailable: str,
        client: Any | None = None,
    ) -> None:
        self.configured = bool(api_key) or client is not None
        self._model = model
        self._unavailable = unavailable
        self._client = client or (AsyncOpenAI(api_key=api_key) if api_key else None)

    async def parse(self, instructions: str, payload: dict[str, Any], shape: type[_T]) -> _T:
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
                503, "SERVICE_UNAVAILABLE", self._unavailable, retryable=True
            ) from exc
