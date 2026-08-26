from __future__ import annotations

from typing import Any, Protocol

from rockygpt_brain.brain.plan.schema import TIME_WORDS, Plan
from rockygpt_brain.capabilities.registry import catalogue
from rockygpt_brain.prompt import beside
from rockygpt_brain.services.openai import StructuredModel

PLAN = beside(__file__)


class PlanPort(Protocol):
    configured: bool

    async def plan(self, resolved: str, current_time: str) -> Plan: ...


class OpenAIPlan:
    def __init__(self, api_key: str | None, model: str, client: Any | None = None) -> None:
        self._model = StructuredModel(
            api_key, model, "The planning service is temporarily unavailable.", client
        )
        self.configured = self._model.configured

    async def plan(self, resolved: str, current_time: str) -> Plan:
        return await self._model.parse(
            PLAN,
            {
                "question": resolved,
                "currentTime": current_time,
                "capabilities": catalogue(),
                "timeWords": list(TIME_WORDS),
            },
            Plan,
        )
