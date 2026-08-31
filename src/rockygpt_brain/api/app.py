"""Minimal HTTP shell for RockyGPT Brain."""

import os
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException
from openai import OpenAI
from openai.types.responses import ResponseInputParam
from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.shuttle import (
    CAMPUS_TIME_ZONE,
    asks_for_next_shuttle,
    next_shuttle_from_database,
    render_next_shuttle_answer,
)

app = FastAPI(title="RockyGPT Brain", version="0.0.0")
MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")


class ChatMessage(BaseModel):
    """One ordered conversation message."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """The complete conversation supplied by the client."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=1)


def campus_now() -> datetime:
    """The injectable clock used by deterministic campus-time capabilities."""
    return datetime.now(CAMPUS_TIME_ZONE)


@app.get("/health")
def health() -> dict[str, str]:
    """Process liveness probe."""
    return {"status": "ok"}


@app.get("/readiness")
def readiness() -> dict[str, str]:
    """Service readiness probe."""
    return {"status": "ready"}


@app.post("/v1/chat")
async def chat(request: ChatRequest) -> dict[str, object]:
    """Answer one ordered conversation turn."""
    messages: ResponseInputParam = [
        {"role": message.role, "content": message.content} for message in request.messages
    ]
    shuttle_fact = None
    if asks_for_next_shuttle(request.messages):
        try:
            shuttle_fact = await next_shuttle_from_database(campus_now())
        except Exception as error:
            raise HTTPException(503, "Trusted shuttle data is unavailable") from error
    if shuttle_fact:
        return {
            "answer": render_next_shuttle_answer(shuttle_fact),
            "model": "deterministic",
            "shuttleFact": shuttle_fact,
        }
    response = OpenAI().responses.create(model=MODEL, input=messages, store=False)
    return {"answer": response.output_text, "model": response.model}
