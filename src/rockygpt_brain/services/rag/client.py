from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class RagUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Passage:
    content: str
    domain: str
    title: str
    url: str


class RagPort(Protocol):
    async def retrieve(self, topic: str, limit: int) -> list[Passage]: ...


class UnavailableRag:
    async def retrieve(self, topic: str, limit: int) -> list[Passage]:
        raise RagUnavailable("DATABASE_URL is not configured")
