"""Small in-memory conversation history and UI log store."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from rockygpt_brain.contracts import (
    ChatLogItem,
    Citation,
    FeedbackRequest,
    LogCitation,
    LogListResponse,
    LogMetrics,
)


@dataclass(slots=True)
class Turn:
    request_id: str
    user: str
    assistant: str
    route: str
    created_at: datetime

    def prompt_value(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "user": self.user,
            "assistant": self.assistant,
            "route": self.route,
            "createdAt": self.created_at.isoformat(),
        }


class MemoryStore:
    """Process-local memory for the BASE implementation."""

    def __init__(self) -> None:
        self._turns: dict[str, list[Turn]] = defaultdict(list)
        self._logs: list[ChatLogItem] = []
        self._version = 0

    def history(self, session_id: str) -> list[dict[str, Any]]:
        return [turn.prompt_value() for turn in self._turns[session_id][-10:]]

    def record(
        self,
        *,
        request_id: str,
        session_id: str,
        visitor_id: str | None,
        question_origin: Literal["client", "dev", "bot"],
        user_message: str,
        assistant_message: str,
        route: str,
        tools: list[str],
        tool_arguments: dict[str, Any],
        citations: list[Citation],
        result: dict[str, Any],
        latency_ms: int,
    ) -> None:
        created_at = datetime.now(UTC)
        self._turns[session_id].append(
            Turn(request_id, user_message, assistant_message, route, created_at)
        )
        self._logs.append(
            ChatLogItem(
                id=request_id,
                session_id=session_id,
                visitor_id=visitor_id,
                user_message=user_message,
                assistant_message=assistant_message,
                route=route,
                question_origin=question_origin,
                tools_invoked=tools,
                tool_arguments=tool_arguments,
                citations=[LogCitation(title=item.title, url=item.url) for item in citations],
                facts_extracted=[],
                debug_info={"result": result},
                latency_ms=latency_ms,
                feedback=None,
                feedback_rating=None,
                feedback_category=None,
                feedback_comment=None,
                created_at=created_at,
            )
        )
        self._version += 1

    def save_feedback(self, feedback: FeedbackRequest) -> None:
        for index, item in enumerate(self._logs):
            if item.id == feedback.request_id:
                self._logs[index] = item.model_copy(
                    update={
                        "feedback_rating": feedback.rating,
                        "feedback_category": feedback.category,
                        "feedback_comment": feedback.comments,
                    }
                )
                self._version += 1
                return

    def set_operator_feedback(self, log_id: str, feedback: str | None) -> bool:
        for index, item in enumerate(self._logs):
            if item.id == log_id:
                self._logs[index] = item.model_copy(update={"feedback": feedback})
                self._version += 1
                return True
        return False

    def list_logs(
        self,
        *,
        search: str | None,
        routes: set[str],
        origins: set[str],
        limit: int,
    ) -> LogListResponse:
        items = list(reversed(self._logs))
        if search:
            needle = search.casefold()
            items = [
                item
                for item in items
                if needle in item.user_message.casefold()
                or needle in item.assistant_message.casefold()
            ]
        if routes:
            items = [item for item in items if item.route in routes]
        if origins:
            items = [item for item in items if item.question_origin in origins]
        selected = items[:limit]
        origins_list = [item.question_origin for item in items]
        return LogListResponse(
            logs=selected,
            metrics=LogMetrics(
                totalLogs=len(items),
                avgLatencyMs=(sum(item.latency_ms for item in items) / len(items) if items else 0),
                uniqueSessions=len({item.session_id for item in items}),
                uniqueVisitors=len(
                    {item.visitor_id for item in items if item.visitor_id is not None}
                ),
                errorCount=0,
                clientCount=origins_list.count("client"),
                devCount=origins_list.count("dev"),
                botCount=origins_list.count("bot"),
            ),
            version=self.version,
        )

    @property
    def version(self) -> str:
        return str(self._version)

    async def changes(self):  # type: ignore[no-untyped-def]
        last = ""
        while True:
            if self.version != last:
                last = self.version
                yield f"data: {json.dumps({'type': 'change', 'version': last})}\n\n"
            await asyncio.sleep(1)
