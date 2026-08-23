"""Process entrypoint. `app` is the ASGI application uvicorn loads (both the
`rockygpt-brain` console script below and a direct `uvicorn
rockygpt_brain.main:app` both target it)."""

from __future__ import annotations

import uvicorn

from rockygpt_brain.app import create_app
from rockygpt_brain.config import get_settings

app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "rockygpt_brain.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.app_env == "development",
    )


if __name__ == "__main__":
    run()
