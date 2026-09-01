"""Minimal HTTP shell for RockyGPT Brain."""

import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.capabilities import ConversationMessage, run_chat

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


@app.get("/health")
def health() -> dict[str, str]:
    """Process liveness probe."""
    return {"status": "ok"}


@app.get("/readiness")
def readiness() -> dict[str, str]:
    """Service readiness probe."""
    return {"status": "ready"}


@app.post("/v1/chat")
def chat(request: ChatRequest) -> dict[str, object]:
    """Run normal chat or deterministically execute a selected shuttle request."""
    messages: list[ConversationMessage] = [
        {"role": message.role, "content": message.content} for message in request.messages
    ]
    try:
        return run_chat(messages, MODEL)
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail=f"Trusted shuttle data is unavailable: {error}",
        ) from error
