"""The Brain, started again from line one: it runs and answers nothing.

There is no chat route, so a question sent to it gets FastAPI's plain 404. The two probes
the apps and run-local.sh look at are here so they can see it up: `/health` (the process
is alive) and `/readiness` (it can be reached; there is nothing else to be ready for yet).
"""

from fastapi import FastAPI

app = FastAPI(title="RockyGPT Brain")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readiness")
def readiness() -> dict[str, str]:
    return {"status": "ready"}
