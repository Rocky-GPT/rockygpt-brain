from __future__ import annotations

from enum import StrEnum


class Concern(StrEnum):
    EMERGENCY = "emergency"
    PRIVACY = "privacy"
    SECRET = "secret"  # noqa: S105
    HARMFUL = "harmful"
