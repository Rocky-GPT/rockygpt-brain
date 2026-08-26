from __future__ import annotations

import json
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from rockygpt_brain.errors import ServiceError, Unavailable, Unsupported

_T = TypeVar("_T", bound=BaseModel)


class StructuredModel:
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
                raise Unsupported(self._unavailable) from exc
            raise Unavailable(self._unavailable) from exc


_EXHAUSTED = ("insufficient_quota", "credit_balance_exhausted", "billing_hard_limit_reached")


def _is_exhausted(exc: Exception) -> bool:
    code = str(getattr(exc, "code", "") or "")
    kind = str(getattr(getattr(exc, "body", None), "get", lambda _k: "")("type") or "")
    haystack = f"{code} {kind} {exc}".lower()
    return any(marker in haystack for marker in _EXHAUSTED)
