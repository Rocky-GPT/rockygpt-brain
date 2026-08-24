"""The BASE hybrid pipeline."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from rockygpt_brain.api.contracts import (
    BrainTrace,
    ChatRequest,
    ChatSuccess,
    Citation,
    UiAction,
    UiActionType,
)
from rockygpt_brain.core.code import CodeExecutor
from rockygpt_brain.core.model import (
    CodeIntent,
    GeneralIntent,
    Intent,
    MemoryIntent,
    ModelPort,
    RagIntent,
    SafetyIntent,
)
from rockygpt_brain.services.data_client import DataPort
from rockygpt_brain.services.memory import MemoryStore

_CODE_ACTIONS = {
    "menu": UiActionType.VIEW_MENU,
    "shuttle": UiActionType.VIEW_BUS,
    "events": UiActionType.VIEW_EVENTS,
    "map": UiActionType.VIEW_MAP,
    "contacts": UiActionType.VIEW_DIRECTORY,
}


@dataclass(slots=True)
class TurnIdentity:
    request_id: str
    session_id: str
    visitor_id: str | None
    question_origin: Literal["client", "dev", "bot"]


class Brain:
    def __init__(self, model: ModelPort, data: DataPort, memory: MemoryStore) -> None:
        self._model = model
        self._code = CodeExecutor(data)
        self._data = data
        self._memory = memory

    async def answer(self, request: ChatRequest, identity: TurnIdentity) -> ChatSuccess:
        started = time.monotonic()
        now = request.now or datetime.now(UTC)
        history = self._memory.history(identity.session_id)
        model_history = [turn.model_dump() for turn in request.history] or history

        # AI #1 — understand the question and choose one explicit lane.
        intent = await self._model.understand(request.message, model_history, now)

        # Python — execute exactly one branch of the hybrid brain.
        result, tools = await self._execute(intent, request.message, history, now)

        # AI #2 — turn the finished JSON result into a human answer.
        draft = await self._model.communicate(
            request.message,
            intent,
            result,
            request.style_mode,
            request.response_mode,
        )

        citations = self._citations(result)
        decision = intent.decision
        action = (
            _CODE_ACTIONS.get(decision.request.action) if isinstance(decision, CodeIntent) else None
        )
        actions = [UiAction(type=action)] if action else []
        response = ChatSuccess(
            requestId=identity.request_id,
            answer=draft.answer,
            route=intent.lane.value,
            citations=citations,
            uiActions=actions,
            suggestedQuestions=draft.suggested_questions[:10],
            brainTrace=BrainTrace(
                input=intent.trace(),
                output=result,
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
            tools=tools,
            tool_arguments=intent.trace(),
            citations=citations,
            result=result,
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
        )
        return response

    async def _execute(
        self,
        intent: Intent,
        message: str,
        history: list[dict[str, Any]],
        now: datetime,
    ) -> tuple[dict[str, Any], list[str]]:
        decision = intent.decision
        if isinstance(decision, CodeIntent):
            return await self._code.execute(decision.request, now), [decision.request.action.value]

        if isinstance(decision, RagIntent):
            return await self._data.retrieve(decision.query, []), ["retrieve"]

        if isinstance(decision, MemoryIntent):
            return {"outcome": "success", "turns": history}, ["memory"]

        if isinstance(decision, GeneralIntent):
            return {
                "outcome": "general",
                "question": message,
                "currentTime": now.isoformat(),
            }, []

        assert isinstance(decision, SafetyIntent)
        return {
            "outcome": "safety",
            "message": (
                "If anyone is in immediate danger, call 911. For a mental-health crisis in "
                "the United States, call or text 988."
            ),
        }, ["safety"]

    @staticmethod
    def _citations(result: dict[str, Any]) -> list[Citation]:
        citations: list[Citation] = []
        evidence = result.get("evidence", [])
        if not isinstance(evidence, list):
            return citations
        for item in evidence:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            try:
                citations.append(
                    Citation(
                        sourceId=item.get("sourceId"),
                        title=item.get("title") or "Campus source",
                        url=item["url"],
                        sourcePath=item.get("sourcePath"),
                        snippet=item.get("snippet"),
                        collectedAt=item.get("collectedAt"),
                    )
                )
            except ValidationError:
                continue
        return citations
