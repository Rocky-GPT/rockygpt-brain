"""Minimal HTTP shell for RockyGPT Brain."""

import os
from typing import Literal

from fastapi import FastAPI
from openai import OpenAI
from openai.types.responses import ResponseInputParam
from pydantic import BaseModel, ConfigDict, Field

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
def chat(request: ChatRequest) -> dict[str, str]:
    """Send the ordered conversation to one model and return its answer."""
    messages: ResponseInputParam = [
        {"role": message.role, "content": message.content} for message in request.messages
    ]
    response = OpenAI().responses.create(model=MODEL, input=messages, store=False)
    return {"answer": response.output_text, "model": response.model}
