"""Minimal HTTP shell for RockyGPT Brain."""

import os

from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel

app = FastAPI(title="RockyGPT Brain", version="0.0.0")
MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")


class ChatRequest(BaseModel):
    """The only chat input supported by the current shell."""

    message: str


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
    """Send one message to one model and return its answer."""
    response = OpenAI().responses.create(model=MODEL, input=request.message, store=False)
    return {"answer": response.output_text, "model": response.model}
