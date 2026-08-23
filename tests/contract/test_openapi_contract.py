"""Acceptance gate: "The OpenAPI document is valid and every runtime route
is represented." spec/brain-api.openapi.yaml is the external contract.

Two independent checks:
1. The spec document itself is a valid OpenAPI document
   (openapi-spec-validator).
2. The set of (METHOD, path) pairs this app actually registers at runtime
   and the set the spec documents are equal — checked as two separate
   set-difference assertions (each with its own message) so a failure says
   which direction broke: an undocumented runtime endpoint, or a
   documented endpoint that was never actually wired up.

"Runtime routes" is read from `app.openapi()` — FastAPI's own generated
schema — rather than by walking `app.routes` by hand. `app.routes` is a
Starlette/FastAPI *internal* structure whose shape has changed between
versions (a nested-router wrapper, not a flat list of leaf routes, as of
the version this was written against); `app.openapi()` is the public,
documented API for "what does this app actually expose," it already
excludes FastAPI's own `/docs`/`/redoc`/`/openapi.json`, and it does not
list the `HEAD` Starlette auto-adds for `GET` routes as a separate
operation — so no extra filtering is needed on the runtime side at all.
"""

from pathlib import Path
from typing import Any

import yaml
from openapi_spec_validator import validate

from rockygpt_brain.app import create_app
from rockygpt_brain.config import Settings

_SPEC_PATH = Path(__file__).resolve().parents[2] / "spec" / "brain-api.openapi.yaml"

_DOCUMENTED_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _load_spec() -> dict[str, Any]:
    return yaml.safe_load(_SPEC_PATH.read_text("utf-8"))


def test_spec_is_a_valid_openapi_document() -> None:
    validate(_load_spec())


def _method_path_pairs(paths: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for path, path_item in paths.items():
        for method in path_item:
            if method in _DOCUMENTED_HTTP_METHODS:
                pairs.add((method.upper(), path))
    return pairs


def test_runtime_routes_match_the_documented_contract_exactly() -> None:
    spec = _load_spec()
    documented = _method_path_pairs(spec["paths"])

    # Admin routes are conditionally mounted; enable them so this check
    # covers the full documented surface, not just the always-public part.
    settings = Settings(ADMIN_API_TOKEN="a" * 32)
    app = create_app(settings=settings)
    runtime = _method_path_pairs(app.openapi()["paths"])

    undocumented_runtime = runtime - documented
    assert not undocumented_runtime, (
        f"Runtime routes exist that are not in the OpenAPI contract: "
        f"{sorted(undocumented_runtime)}"
    )

    unimplemented_documented = documented - runtime
    assert not unimplemented_documented, (
        f"OpenAPI contract documents routes that are not implemented at "
        f"runtime: {sorted(unimplemented_documented)}"
    )
