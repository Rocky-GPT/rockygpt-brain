from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

from rockygpt_brain.api.contracts import (
    HISTORY_EXCHANGES,
    ChatLogItem,
    Citation,
    FeedbackRequest,
    LogCitation,
    LogListResponse,
    LogMetrics,
)
from rockygpt_brain.context.schema import Turn


class MemoryStore:
    def __init__(self) -> None:
        self._turns: dict[str, list[Turn]] = defaultdict(list)
        self._logs: list[ChatLogItem] = []
        self._version = 0

    def history(self, session_id: str) -> list[dict[str, Any]]:
        return [turn.prompt_value() for turn in self._turns[session_id][-HISTORY_EXCHANGES:]]

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
        if assistant_message:
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
                total_logs=len(items),
                avg_latency_ms=(
                    sum(item.latency_ms for item in items) / len(items) if items else 0
                ),
                unique_sessions=len({item.session_id for item in items}),
                unique_visitors=len(
                    {item.visitor_id for item in items if item.visitor_id is not None}
                ),
                error_count=0,
                client_count=origins_list.count("client"),
                dev_count=origins_list.count("dev"),
                bot_count=origins_list.count("bot"),
            ),
            version=self.version,
        )

    @property
    def version(self) -> str:
        return str(self._version)

    async def changes(self) -> AsyncIterator[str]:
        last = ""
        while True:
            if self.version != last:
                last = self.version
                yield f"data: {json.dumps({'type': 'change', 'version': last})}\n\n"
            await asyncio.sleep(1)
