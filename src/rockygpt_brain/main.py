"""Console entry point."""

from __future__ import annotations

import uvicorn

from rockygpt_brain.config import get_settings


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "rockygpt_brain.app:app",
        host=settings.host,
        port=settings.port,
        factory=False,
    )


if __name__ == "__main__":
    run()
