from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from rockygpt_brain.services.data import DataUnavailable


@dataclass(frozen=True, slots=True)
class PublishedArtifact:
    """One immutable artifact from the active published dataset."""

    payload: Any
    release_version: str
    activated_at: str | None = None
    content_hash: str | None = None
    source: str = "postgres"


class ArtifactPort(Protocol):
    async def artifact(self, key: str) -> PublishedArtifact: ...


class UnavailableArtifacts:
    async def artifact(self, key: str) -> PublishedArtifact:
        raise DataUnavailable("DATABASE_URL is not configured")
