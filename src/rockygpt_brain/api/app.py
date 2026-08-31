"""Minimal HTTP shell for RockyGPT Brain."""

from fastapi import FastAPI

app = FastAPI(title="RockyGPT Brain", version="0.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Process liveness probe."""
    return {"status": "ok"}


@app.get("/readiness")
def readiness() -> dict[str, str]:
    """Service readiness probe."""
    return {"status": "ready"}


@app.get("/readiness/chat-logs")
def readiness_chat_logs() -> dict[str, str]:
    """Chat logs readiness probe."""
    return {"status": "ready"}
