"""Running one corpus case against one DataPort, and writing down what happened.

The sequence here is `lanes/code/run.py`'s, not an approximation of it:
normalize first, then execute, both handed the same port. Entity resolution
runs inside normalize and reads the port itself, so a harness that called only
`execute` would leave the resolution queries — the ones that decide which
route or venue the rest of the lookup is about — untested.

Two layers come back from every case:

  port_calls  what the implementation was asked and what it returned
  output      what the capability produced from that

They answer different questions. A difference in `port_calls` says the port is
the source. Identical `port_calls` with differing `output` says something
downstream is not deterministic, which is a finding about this harness or about
shared code, never about the migration.

Calls are keyed by method and canonical query rather than kept in call order,
because `hours` gathers campus and dining concurrently and the completion order
is not the implementation's to control. Keyed, an interleaving is not a diff.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from tools.differential.corpus import Case

from rockygpt_brain.brain.plan.validate import _normalize_value
from rockygpt_brain.capabilities.entities import EntityResolutionFailed
from rockygpt_brain.capabilities.registry import Capability, capability_for
from rockygpt_brain.services.data import DataPort, DataUnavailable


def canonical(value: Any) -> str:
    """A stable string for any JSON-shaped value, for use as a key or a hash."""
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


class CorpusRejected(Exception):
    """A corpus filter the planner's own validation would have refused."""


def validated(case_filters: Mapping[str, str], entry: Capability, now: datetime) -> dict[str, str]:
    """Corpus filters as the lane would receive them, not as they are written.

    `plan.validate` canonicalises every filter before any capability sees it:
    an instant written `8:00 PM` reaches `normalize` as a full ISO timestamp,
    and a date written `today` reaches it as a calendar date. A corpus that
    handed capabilities its own raw spellings would exercise port queries the
    brain never sends — the first draft of this harness did exactly that, and
    two cases 400ed against the data service for a reason no migration would
    ever reproduce.

    `_normalize_value` is borrowed rather than reimplemented. A second copy of
    this rule would be a second answer to what a filter means, and it would be
    the copy that drifted.
    """
    out: dict[str, str] = {}
    for name, raw in case_filters.items():
        spec = entry.filters.get(name)
        if spec is None:
            raise CorpusRejected(f"{name!r} is not a filter this capability accepts")
        value = _normalize_value(raw, spec, now)
        if value is None:
            raise CorpusRejected(f"{name}={raw!r} is not a valid {spec.kind.value}")
        out[name] = value
    return out


@dataclass
class PortCall:
    method: str
    query: dict[str, Any]
    records: list[dict[str, Any]] | None = None
    error: str | None = None

    @property
    def key(self) -> str:
        return f"{self.method}({canonical(self.query)})"

    def as_json(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "query": self.query,
            "records": self.records,
            "error": self.error,
        }


@dataclass
class Capture:
    """Everything one case produced against one implementation."""

    case: str
    capability: str
    covers: str
    port_calls: dict[str, PortCall] = field(default_factory=dict)
    output: list[dict[str, Any]] | None = None
    # A lookup that could not run is a result worth comparing. Two ports that
    # both fail the same way agree; one that fails where the other answers is
    # the loudest finding this harness can produce.
    failure: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "capability": self.capability,
            "covers": self.covers,
            "portCalls": {key: call.as_json() for key, call in sorted(self.port_calls.items())},
            "output": self.output,
            "failure": self.failure,
        }

    @staticmethod
    def from_json(body: Mapping[str, Any]) -> Capture:
        calls: dict[str, PortCall] = {}
        for key, raw in dict(body.get("portCalls") or {}).items():
            calls[key] = PortCall(
                method=str(raw.get("method", "")),
                query=dict(raw.get("query") or {}),
                records=raw.get("records"),
                error=raw.get("error"),
            )
        return Capture(
            case=str(body.get("case", "")),
            capability=str(body.get("capability", "")),
            covers=str(body.get("covers", "")),
            port_calls=calls,
            output=body.get("output"),
            failure=body.get("failure"),
        )


class RecordingPort:
    """A DataPort that answers from `inner` and writes down every exchange.

    Each method is spelled out rather than generated, for the same reason
    `HttpData`'s are: the Protocol is the contract, and a port that silently
    grew a method the Protocol does not name should fail to type-check here
    too.
    """

    def __init__(self, inner: DataPort) -> None:
        self._inner = inner
        self.calls: dict[str, PortCall] = {}

    async def _record(
        self,
        method: str,
        query: dict[str, Any],
        call: Awaitable[list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        entry = PortCall(method=method, query=dict(query))
        try:
            records = await call
        except DataUnavailable as exc:
            entry.error = f"DataUnavailable: {exc}"
            self.calls[entry.key] = entry
            raise
        entry.records = records
        self.calls[entry.key] = entry
        return records

    async def shuttle(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._record("shuttle", query, self._inner.shuttle(query))

    async def dining(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("dining", query, self._inner.dining(query))

    async def events(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("events", query, self._inner.events(query))

    async def campus_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("campus_hours", query, self._inner.campus_hours(query))

    async def dining_hours(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("dining_hours", query, self._inner.dining_hours(query))

    async def courses(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("courses", query, self._inner.courses(query))

    async def course_subjects(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("course_subjects", query, self._inner.course_subjects(query))

    async def transportation(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return await self._record("transportation", query, self._inner.transportation(query))

    async def calendar(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("calendar", query, self._inner.calendar(query))

    async def clubs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("clubs", query, self._inner.clubs(query))

    async def directory(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("directory", query, self._inner.directory(query))

    async def locations(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("locations", query, self._inner.locations(query))

    async def programs(self, query: dict[str, str]) -> list[dict[str, Any]]:
        return await self._record("programs", query, self._inner.programs(query))


async def capture(case: Case, port: DataPort) -> Capture:
    """Run one case and return what the implementation did with it."""
    entry = capability_for(case.capability)
    if entry is None:
        return Capture(
            case=case.name,
            capability=case.capability,
            covers=case.covers,
            failure=f"no capability named {case.capability!r}",
        )

    recorder = RecordingPort(port)
    result = Capture(case=case.name, capability=case.capability, covers=case.covers)
    try:
        semantic = validated(case.filters, entry, case.now)
    except CorpusRejected as exc:
        # Not a divergence — a case that could never have run. Fail it loudly
        # here rather than let it compare equal as two empty results.
        result.failure = f"CorpusRejected: {exc}"
        return result
    try:
        execution_filters = (
            await entry.normalize(semantic, case.now, recorder)
            if entry.normalize is not None
            else semantic
        )
        result.output = await entry.execute(execution_filters, case.now, recorder)
    except EntityResolutionFailed as exc:
        result.failure = f"EntityResolutionFailed: {exc}"
    except DataUnavailable as exc:
        result.failure = f"DataUnavailable: {exc}"
    result.port_calls = recorder.calls
    return result
