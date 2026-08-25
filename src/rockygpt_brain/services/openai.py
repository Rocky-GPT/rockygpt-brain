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

from rockygpt_brain.errors import ServiceError, Unavailable, Unsupported

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
            # Not retryable: no number of attempts sets an environment variable.
            raise Unsupported("OPENAI_API_KEY is not configured.")
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
            if _is_exhausted(exc):
                # A spent balance is not a hiccup. Telling the client to try
                # again sends it back forever against something only a person
                # with the billing page can fix — which is what happened, and
                # took three probes to see, because the message said the
                # service was "temporarily unavailable".
                raise Unsupported(self._unavailable) from exc
            raise Unavailable(self._unavailable) from exc


#: What the provider calls having nothing left to spend. A rate limit proper —
#: too many requests in a window — is a different thing and does clear on its
#: own, so it is deliberately not matched here.
_EXHAUSTED = ("insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached")


def _is_exhausted(exc: Exception) -> bool:
    """Whether this failure is a spent account rather than a passing fault."""
    code = str(getattr(exc, "code", "") or "")
    kind = str(getattr(getattr(exc, "body", None), "get", lambda _k: "")("type") or "")
    haystack = f"{code} {kind} {exc}".lower()
    return any(marker in haystack for marker in _EXHAUSTED)
