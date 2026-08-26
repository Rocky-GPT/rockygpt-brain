from __future__ import annotations

from collections.abc import Callable
from typing import Any

Reader = Callable[[dict[str, Any]], Any]
