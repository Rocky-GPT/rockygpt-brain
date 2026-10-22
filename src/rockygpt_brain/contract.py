"""What the apps send to `POST /v1/chat` (docs/contract.md).

The shape is the one the student app and the dev UI already send. Anything else is refused
before a turn exists, so the rest of the Brain only ever sees a request that fits.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator

MAX_MESSAGES = 80
MAX_MESSAGE_CHARS = 16_000
MAX_CONVERSATION_CHARS = 48_000
MAX_OMITTED_MESSAGES = 100_000
CONVERSATION_ID_HEADER = "x-rockygpt-conversation-id"
CONVERSATION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

Content = Annotated[str, StringConstraints(min_length=1, max_length=MAX_MESSAGE_CHARS)]


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: Content

    @model_validator(mode="after")
    def not_blank(self) -> Self:
        if not self.content.strip():
            raise ValueError("content is blank")
        return self


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: Annotated[list[ChatMessage], Field(min_length=1, max_length=MAX_MESSAGES)]
    omittedMessages: Annotated[StrictInt, Field(ge=0, le=MAX_OMITTED_MESSAGES)] = 0

    @model_validator(mode="after")
    def fits_a_conversation(self) -> Self:
        if self.messages[0].role != "user" or self.messages[-1].role != "user":
            raise ValueError("messages must start and end with a user message")
        if sum(len(message.content) for message in self.messages) > MAX_CONVERSATION_CHARS:
            raise ValueError(f"messages are over {MAX_CONVERSATION_CHARS} characters together")
        return self
