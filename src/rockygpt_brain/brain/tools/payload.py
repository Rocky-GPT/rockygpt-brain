"""The shapes a tool call passes around: what a handler returns, and how a
tool advertises itself.

Kept separate from both the handlers and the schema table so neither has to
import the other to name a type.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from rockygpt_brain.brain.time_context import TimeContext
from rockygpt_brain.data_client.client import DataServiceClient
from rockygpt_brain.data_client.models import SearchResult


@dataclass(frozen=True, slots=True)
class ToolPayload:
    """What a tool handler returns, before summarizing.

    Most endpoints answer with the dataset-versioned `SearchResult`
    envelope, but `/v1/map` does not — it returns bare locations with no
    dataset and no per-record `source`. This is the shape both can take,
    so `summarize` stays a single code path rather than growing a
    per-endpoint special case.
    """

    records: list[dict[str, Any]]
    dataset_id: str | None = None
    dataset_version: str | None = None

    @classmethod
    def from_search(cls, result: SearchResult) -> ToolPayload:
        return cls(
            records=result.records,
            dataset_id=result.dataset.id,
            dataset_version=result.dataset.version,
        )


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[DataServiceClient, TimeContext, dict[str, Any]], Awaitable[ToolPayload]]
