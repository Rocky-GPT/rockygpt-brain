"""Ask BRAIN #2 what to do about the question.

It is given the resolved question and nothing else — no conversation, no
original wording. Two calls rather than one so that is a fact about what the
model can read rather than a line in an instruction it may or may not heed. A
plan that would have needed the conversation is a plan built on a resolution
that failed, and `understand.validate` is where that shows.

Before editing `prompt.md`: adding the safety paragraph to it once moved lane
routing across the whole 30-question set. Measure before and after. It
describes lanes, fields and operations and contains no worked example — the
moment a question shape appears there, the translator has become a list of
intents and the next question needs code again.
"""

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
                # Only what can actually run. A capability listed here without
                # code behind it is a turn that fails at execution.
                "capabilities": catalogue(),
                "timeWords": list(TIME_WORDS),
            },
            Plan,
        )
