from __future__ import annotations

import subprocess
import uvicorn

from rockygpt_brain.api.app import app as app
from rockygpt_brain.config import get_settings


def _free_port(port: int) -> None:
    try:
        pids = subprocess.check_output(["lsof", "-ti", f":{port}"], text=True).strip().split()
        if pids:
            subprocess.run(["kill", "-9", *pids], check=False)
    except Exception:
        pass


def run() -> None:
    settings = get_settings()
    _free_port(settings.port)
    uvicorn.run(
        "rockygpt_brain.api.app:app",
        host=settings.host,
        port=settings.port,
        factory=False,
    )


if __name__ == "__main__":
    run()

