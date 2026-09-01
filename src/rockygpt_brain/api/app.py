"""Minimal HTTP shell for RockyGPT Brain."""

import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from rockygpt_brain.transportation import ShuttleComparisonRequest, ShuttleQueryRequest
from rockygpt_brain.transportation_execution import (
    answer_transportation,
    execute_transportation,
    load_trusted_shuttle_data,
    route_mentions_match_trusted_data,
)
from rockygpt_brain.transportation_interpretation import (
    ConversationMessage,
    interpret_transportation,
    interpretation_failure,
    repair_transportation_interpretation,
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
    """Run normal chat or deterministically execute a selected shuttle request."""
    messages: list[ConversationMessage] = [
        {"role": message.role, "content": message.content} for message in request.messages
    ]
    answer, interpretation = interpret_transportation(messages, MODEL)
    transportation_result = None
    transportation_provenance = None
    if interpretation.selected:
        transportation_request = interpretation.request
        assert transportation_request is not None
        try:
            trusted_data = None
            if isinstance(
                transportation_request,
                (ShuttleQueryRequest, ShuttleComparisonRequest),
            ):
                trusted_data = load_trusted_shuttle_data()
                if not route_mentions_match_trusted_data(
                    transportation_request,
                    trusted_data,
                ):
                    _, interpretation = repair_transportation_interpretation(messages, MODEL)
                    transportation_request = interpretation.request
                    assert transportation_request is not None
                    if isinstance(
                        transportation_request,
                        (ShuttleQueryRequest, ShuttleComparisonRequest),
                    ) and not route_mentions_match_trusted_data(
                        transportation_request,
                        trusted_data,
                    ):
                        _, interpretation = interpretation_failure(interpretation.model)
                        transportation_request = interpretation.request
                        assert transportation_request is not None
            transportation_result = execute_transportation(
                transportation_request,
                data=trusted_data,
            )
        except RuntimeError as error:
            raise HTTPException(
                status_code=503,
                detail=f"Trusted shuttle data is unavailable: {error}",
            ) from error
        transportation_provenance = transportation_result.provenance
        answer = answer_transportation(transportation_result)
    return {
        "answer": answer,
        "model": interpretation.model,
        "transportationInterpretation": interpretation.model_dump(mode="json"),
        "transportationResult": (
            transportation_result.model_dump(mode="json")
            if transportation_result is not None
            else None
        ),
        "transportationProvenance": (
            transportation_provenance.model_dump(mode="json")
            if transportation_provenance is not None
            else None
        ),
    }
