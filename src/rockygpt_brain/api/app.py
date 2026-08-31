"""Minimal HTTP shell for RockyGPT Brain."""

import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.transportation_interpretation import (
    ConversationMessage,
    InvalidTransportationInterpretation,
    interpret_transportation,
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
    """Interpret transportation or return the model's normal chat answer."""
    messages: list[ConversationMessage] = [
        {"role": message.role, "content": message.content} for message in request.messages
    ]
    try:
        answer, interpretation = interpret_transportation(messages, MODEL)
    except InvalidTransportationInterpretation as error:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "The model returned an invalid transportation interpretation.",
                "reason": str(error),
            },
        ) from error
    return {
        "answer": answer,
        "model": interpretation.model,
        "transportationInterpretation": interpretation.model_dump(mode="json"),
    }
