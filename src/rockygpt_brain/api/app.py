"""Minimal HTTP shell for RockyGPT Brain."""

import os
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.capabilities import ConversationMessage, classify

app = FastAPI(title="RockyGPT Brain", version="0.0.0")
MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o")


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


@app.post("/v1/chat", response_model=None)
def chat(request: ChatRequest) -> dict[str, object] | JSONResponse:
    """Classify the conversation into ordered unique capability labels."""
    messages: list[ConversationMessage] = [
        {"role": message.role, "content": message.content} for message in request.messages
    ]
    try:
        capabilities, model = classify(messages, MODEL)
    except RateLimitError:
        return JSONResponse(
            status_code=429,
            content={
                "error": "The classifier model is temporarily rate limited.",
                "reason": "rate_limited",
                "detail": "No classification was produced. Try this request again later.",
                "retryable": True,
            },
        )
    except APITimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "error": "The classifier model timed out.",
                "reason": "model_timeout",
                "detail": "No classification was produced before the provider timeout.",
                "retryable": True,
            },
        )
    except APIConnectionError:
        return JSONResponse(
            status_code=503,
            content={
                "error": "The classifier model is unreachable.",
                "reason": "model_unreachable",
                "detail": "No classification was produced because the provider connection failed.",
                "retryable": True,
            },
        )
    except APIStatusError as error:
        return JSONResponse(
            status_code=502,
            content={
                "error": "The classifier model rejected the request.",
                "reason": "model_provider_error",
                "detail": "No classification was produced by the provider.",
                "upstream_status": error.status_code,
                "retryable": error.status_code >= 500,
            },
        )
    return {
        "answer": "\n\n".join(capabilities),
        "capabilities": list(capabilities),
        "model": model,
    }
