"""Request-local wall-clock spans, partitioned into non-overlapping microseconds.

Async tasks and evidence worker threads inherit the recorder through contextvars.
Nested work replaces its parent's time; it is never added on top of that time.
"""

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from typing import Any


@dataclass
class Span:
    label: str
    start: int
    depth: int
    parent: int | None
    end: int | None = None
    status: str = "running"


class Timeline:
    def __init__(self) -> None:
        self.started = time.perf_counter_ns()
        self.spans: list[Span] = []
        self.lock = Lock()
        self.closed = False

    def elapsed_us(self) -> int:
        return (time.perf_counter_ns() - self.started) // 1_000

    def begin(self, label: str, parent: int | None) -> int | None:
        with self.lock:
            if self.closed:
                return None
            index = len(self.spans)
            depth = self.spans[parent].depth + 1 if parent is not None else 0
            self.spans.append(Span(label, self.elapsed_us(), depth, parent))
            return index

    def end(self, index: int, status: str) -> None:
        with self.lock:
            if not self.closed:
                self.spans[index].end = self.elapsed_us()
                self.spans[index].status = status

    def report(self) -> dict[str, Any]:
        with self.lock:
            total = self.elapsed_us()
            self.closed = True  # Late worker completion cannot mutate the saved report.
            spans = list(self.spans)
        ends: list[int] = []
        for span in spans:
            end = min(span.end if span.end is not None else total, total)
            # A timed-out worker may still finish its database read in the background.
            # Its work after the awaiting parent ends is outside the response path.
            ends.append(min(end, ends[span.parent]) if span.parent is not None else end)
        boundaries = sorted({0, total, *(min(span.start, total) for span in spans), *ends})
        parents = {span.parent for span in spans if span.parent is not None}
        rows: list[dict[str, Any]] = []
        for start, end in zip(boundaries, boundaries[1:], strict=False):
            if start == end:
                continue
            active = [i for i, span in enumerate(spans) if span.start <= start and ends[i] >= end]
            selected = (max(active, key=lambda i: (spans[i].depth, spans[i].start, i))
                        if active else None)
            label, status = "Brain request handling", "ok"
            if selected is not None:
                span = spans[selected]
                label, status = span.label, span.status
                # Preserve the model/lookup call number on its measured substeps.
                ancestor = span.parent
                while ancestor is not None:
                    name = spans[ancestor].label
                    if name.startswith(("Model call ", "Lookup ")):
                        label = f"{name} · {label}"
                    ancestor = spans[ancestor].parent
                if selected in parents:
                    label += " · coordination"
                if status == "running":
                    status = "open_at_response"
                if ends[selected] < min(span.end if span.end is not None else total, total):
                    status = "interrupted"
            if rows and rows[-1]["spanId"] == selected:
                rows[-1]["durationUs"] += end - start
                rows[-1]["endUs"] = end
            else:
                rows.append({"spanId": selected, "label": label, "status": status,
                             "startUs": start, "endUs": end, "durationUs": end - start})
        return {"unit": "microseconds", "totalUs": total, "steps": rows,
                "accounting": "exclusive_wall_time"}


_timeline: ContextVar[Timeline | None] = ContextVar("brain_timeline", default=None)
_parent: ContextVar[int | None] = ContextVar("brain_timing_parent", default=None)


@contextmanager
def request_timing() -> Iterator[Timeline]:
    timeline = Timeline()
    token, parent_token = _timeline.set(timeline), _parent.set(None)
    try:
        yield timeline
    finally:
        _parent.reset(parent_token)
        _timeline.reset(token)


@contextmanager
def measure(label: str) -> Iterator[None]:
    timeline = _timeline.get()
    index = timeline.begin(label, _parent.get()) if timeline is not None else None
    if timeline is None or index is None:
        yield
        return
    token = _parent.set(index)
    status = "ok"
    try:
        yield
    except BaseException as error:
        status = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
        raise
    finally:
        timeline.end(index, status)
        _parent.reset(token)
