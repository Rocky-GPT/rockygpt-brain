"""Types shared by structured CODE capabilities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Reader = Callable[[dict[str, Any]], Any]
