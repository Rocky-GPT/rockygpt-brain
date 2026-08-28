from __future__ import annotations

import contextlib
import subprocess

import uvicorn

from rockygpt_brain.api.app import app as app
from rockygpt_brain.config import get_settings


def _free_port(port: int) -> None:
    with contextlib.suppress(Exception):
        pids = (
            subprocess.check_output(  # noqa: S603
                ["lsof", "-ti", f":{port}"],  # noqa: S607
                text=True,
            )
            .strip()
            .split()
        )
        if pids:
            subprocess.run(["kill", "-9", *pids], check=False)  # noqa: S603, S607


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

