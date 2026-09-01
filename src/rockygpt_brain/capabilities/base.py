"""Small shared contract for dynamically discovered Brain capabilities."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict


class ConversationMessage(TypedDict):
    """One original ordered chat message."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class CapabilityRun:
    """One capability's selection decision and inspectable response fields."""

    selected: bool
    answer: str
    model: str
    inspection: dict[str, object]


class Capability(Protocol):
    """The complete interface required from a capability package."""

    name: str

    def run(
        self, messages: Sequence[ConversationMessage], model: str
    ) -> CapabilityRun: ...
