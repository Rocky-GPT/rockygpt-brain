"""Minimal HTTP shell for RockyGPT Brain."""

import json
import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from openai import OpenAI
from openai.types.responses import ResponseInputParam
from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.shuttle import asks_for_next_shuttle, next_shuttle_from_database

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
async def chat(request: ChatRequest) -> dict[str, object]:
    """Send the ordered conversation to one model and return its answer."""
    messages: ResponseInputParam = [
        {"role": message.role, "content": message.content} for message in request.messages
    ]
    shuttle_fact = None
    if asks_for_next_shuttle(request.messages):
        try:
            shuttle_fact = await next_shuttle_from_database()
        except Exception as error:
            raise HTTPException(503, "Trusted shuttle data is unavailable") from error
    model_input: ResponseInputParam = messages
    if shuttle_fact:
        model_input = [
            {
                "role": "developer",
                "content": (
                    "Answer the latest shuttle question using only this deterministic fact. "
                    "Phrase it naturally, say the time is scheduled, and do not invent other "
                    "shuttle details.\n"
                    + json.dumps(shuttle_fact, separators=(",", ":"))
                ),
            },
            *messages,
        ]
    response = OpenAI().responses.create(model=MODEL, input=model_input, store=False)
    result: dict[str, object] = {"answer": response.output_text, "model": response.model}
    if shuttle_fact:
        result["shuttleFact"] = shuttle_fact
    return result
