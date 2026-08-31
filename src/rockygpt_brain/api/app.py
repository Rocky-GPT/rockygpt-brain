"""Minimal HTTP shell for RockyGPT Brain."""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="RockyGPT Brain", version="0.0.0")


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
def chat(_: ChatRequest) -> dict[str, str]:
    """Return the fixed response used to verify the chat connection."""
    return {"answer": "RockyGPT chat is connected."}
