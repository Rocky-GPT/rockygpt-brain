"""Development-only read routes for the dev UI: what the Brain is set to do, and what it publishes.

Registered only when the Brain runs in development, and each route also needs the
`x-rockygpt-diagnostics: 1` header the dev UI sends. In production the routes do not exist.
They are left out of the OpenAPI schema, so the route count the dev UI shows stays the real surface.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from rockygpt_brain.engine import lookup_choice
from rockygpt_brain.failures import failure
from rockygpt_brain.retrieval import EvidenceUnavailable, InvalidFactRequest
from rockygpt_brain.turn import new_id

DIAGNOSTICS_HEADER = "x-rockygpt-diagnostics"
NO_STORE = {"Cache-Control": "no-store"}


def add_dev_routes(application: FastAPI) -> None:
    def asked(diagnostics: Annotated[str | None, Header(alias=DIAGNOSTICS_HEADER)] = None) -> None:
        if diagnostics != "1":
            raise HTTPException(404)

    router = APIRouter(prefix="/v1/dev", dependencies=[Depends(asked)], include_in_schema=False)

    @router.get("/runtime")
    async def runtime() -> JSONResponse:
        engine = application.state.engine
        if engine is None:
            return failure(503, "model_not_configured", "RockyGPT isn't configured.", new_id())
        return JSONResponse(engine.runtime(), headers=NO_STORE)

    @router.get("/offices")
    async def offices() -> JSONResponse:
        engine = application.state.engine
        if engine is None:
            return failure(503, "data_unavailable", "Campus data is unavailable.", new_id())
        try:
            async with asyncio.timeout(5):
                listing = await asyncio.to_thread(engine.facts.list_offices, with_ids=True)
        except (EvidenceUnavailable, TimeoutError):
            return failure(503, "data_unavailable", "Campus data is unavailable.", new_id(),
                           retryable=True)
        return JSONResponse({
            "datasetVersion": listing["dataset_version"], "identityHash": listing["identity_hash"],
            "truncated": listing["truncated"],
            "offices": [{"entityId": o["entity_id"], "name": o["name"], "aliases": o["aliases"]}
                        for o in listing["offices"]],
        }, headers=NO_STORE)

    @router.get("/offices/search")
    async def search(q: Annotated[str, Query(min_length=1, max_length=200)]) -> JSONResponse:
        engine = application.state.engine
        if engine is None:
            return failure(503, "data_unavailable", "Campus data is unavailable.", new_id())
        try:
            async with asyncio.timeout(5):
                found = await asyncio.to_thread(engine.facts.search_offices, q)
        except InvalidFactRequest:
            return failure(422, "invalid_request", "The search text isn't valid.", new_id())
        except (EvidenceUnavailable, TimeoutError):
            return failure(503, "data_unavailable", "Campus data is unavailable.", new_id(),
                           retryable=True)
        outcome, chosen = lookup_choice(found["candidates"], found["truncated"])
        return JSONResponse({
            "query": q, "datasetVersion": found["dataset_version"],
            "identityHash": found["identity_hash"], "truncated": found["truncated"],
            # What a lookup does with this result, decided by the engine's own function:
            # answer for one office, ask which, or find none.
            "outcome": outcome,
            "chosen": [c["name"] for c in chosen] if outcome == "answers" else [],
            "candidates": [{"entityId": c["entity_id"], "name": c["name"], "match": c["match"]}
                           for c in found["candidates"]],
        }, headers=NO_STORE)

    application.include_router(router)
