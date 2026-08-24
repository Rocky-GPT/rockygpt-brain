"""The turn. Contract sections 2, 4.2, 6.1 and 9.

One Interpretation, one result per task, one sealed outcome each, then prose. The
ordering is the contract: nothing reaches the Writer that still contains a
decision, and nothing leaves the Writer that was not already decided.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from rockygpt_brain.api.contracts import (
    BrainTrace,
    ChatRequest,
    ChatSuccess,
    Citation,
    UiAction,
    UiActionType,
)
from rockygpt_brain.core.executor import Executor
from rockygpt_brain.core.interpretation import Domain
from rockygpt_brain.core.model import ModelPort
from rockygpt_brain.core.outcomes import Outcome, Success
from rockygpt_brain.core.safety import safety_block
from rockygpt_brain.services.data_client import DataPort
from rockygpt_brain.services.memory import MemoryStore

_UI_ACTIONS: dict[Domain, UiActionType] = {
    Domain.MENU: UiActionType.VIEW_MENU,
    Domain.SHUTTLE: UiActionType.VIEW_BUS,
    Domain.EVENTS: UiActionType.VIEW_EVENTS,
    Domain.MAP: UiActionType.VIEW_MAP,
    Domain.CONTACTS: UiActionType.VIEW_DIRECTORY,
}


@dataclass(slots=True)
class TurnIdentity:
    request_id: str
    session_id: str
    visitor_id: str | None
    question_origin: Literal["client", "dev", "bot"]


class TaskAccounting(Exception):
    """A turn produced a different number of results than it received tasks."""


class Brain:
    def __init__(
        self,
        model: ModelPort,
        data: DataPort,
        memory: MemoryStore,
        timezone: str = "America/New_York",
    ) -> None:
        self._model = model
        self._executor = Executor(data)
        self._memory = memory
        self._tz = ZoneInfo(timezone)

    async def answer(self, request: ChatRequest, identity: TurnIdentity) -> ChatSuccess:
        started = time.monotonic()
        now = request.now or datetime.now(UTC)
        history = self._memory.history(identity.session_id)
        model_history = [turn.model_dump() for turn in request.history] or history

        # Listener — meaning only.
        interpretation = await self._model.understand(request.message, model_history, now)

        # Safety is a property of the turn, not one of its routes: the block is
        # assembled in code and prepended whatever else the turn contains.
        emergency = safety_block(interpretation.danger)

        # Worker — one sealed outcome per task.
        outcomes = [await self._executor.run(task, now, self._tz) for task in interpretation.tasks]
        if len(outcomes) != len(interpretation.tasks):
            raise TaskAccounting("a task was dropped between interpretation and execution")

        results = [
            {"task": task.model_dump(mode="json", exclude_none=True), "outcome": _json(outcome)}
            for task, outcome in zip(interpretation.tasks, outcomes, strict=True)
        ]

        # Writer — prose only.
        draft = await self._model.communicate(
            request.message,
            results,
            emergency is not None,
            request.style_mode,
            request.response_mode,
        )
        answer = f"{emergency}\n\n{draft.answer}" if emergency else draft.answer

        citations = self._citations(outcomes)
        primary = Domain(interpretation.tasks[0].domain)
        action = _UI_ACTIONS.get(primary)
        response = ChatSuccess(
            request_id=identity.request_id,
            answer=answer,
            route=primary.value,
            citations=citations,
            ui_actions=[UiAction(type=action)] if action else [],
            suggested_questions=draft.suggested_questions[:10],
            brain_trace=BrainTrace(
                input=interpretation.model_dump(mode="json", exclude_none=True),
                output={"results": results, "danger": interpretation.danger.value},
            ),
        )
        self._memory.record(
            request_id=identity.request_id,
            session_id=identity.session_id,
            visitor_id=identity.visitor_id,
            question_origin=identity.question_origin,
            user_message=request.message,
            assistant_message=response.answer,
            route=response.route,
            tools=[Domain(task.domain).value for task in interpretation.tasks],
            tool_arguments=response.brain_trace.input,
            citations=citations,
            result=response.brain_trace.output,
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
        )
        return response

    @staticmethod
    def _citations(outcomes: list[Outcome]) -> list[Citation]:
        """Only evidence attached to a successful result may be cited."""

        citations: list[Citation] = []
        for outcome in outcomes:
            if not isinstance(outcome, Success):
                continue
            for item in outcome.evidence:
                if not isinstance(item, dict) or not item.get("url"):
                    continue
                try:
                    citations.append(
                        Citation(
                            source_id=item.get("sourceId"),
                            title=item.get("title") or "Campus source",
                            url=item["url"],
                            source_path=item.get("sourcePath"),
                            snippet=item.get("snippet"),
                            collected_at=item.get("collectedAt"),
                        )
                    )
                except ValidationError:
                    continue
        return citations


def _json(outcome: Outcome) -> dict[str, Any]:
    return outcome.model_dump(mode="json", by_alias=True, exclude_none=True)
