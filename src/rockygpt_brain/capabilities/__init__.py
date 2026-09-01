"""Public boundary for RockyGPT capability classification."""

from rockygpt_brain.capabilities.classifier import (
    CAPABILITY_LABELS,
    CapabilityLabel,
    ConversationMessage,
    classify,
)

__all__ = [
    "CAPABILITY_LABELS",
    "CapabilityLabel",
    "ConversationMessage",
    "classify",
]
