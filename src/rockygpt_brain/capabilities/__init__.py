"""Dynamic composition boundary for optional RockyGPT capabilities."""

from rockygpt_brain.capabilities.base import ConversationMessage
from rockygpt_brain.capabilities.runtime import run_chat

__all__ = ["ConversationMessage", "run_chat"]
