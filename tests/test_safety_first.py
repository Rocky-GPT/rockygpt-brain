"""The code-written safety block reaches a streaming student before anything else (09-29).

Original Q30 read danger at once, but the call-911 block arrived only with the final
answer at 33.8 s, after lookups, three drafts and review.
"""

import asyncio
import json
from threading import Event
from time import monotonic
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.responses import JSONResponse

from rockygpt_brain.api.stream import stream_turn
from rockygpt_brain.campus.formats import SAFETY_NET
from rockygpt_brain.campus.progress import ProgressUpdate, WorkLog
from rockygpt_brain.config import RELEASE
from rockygpt_brain.core.engine import run_turn
from test_engine import NOW, answer, review
from test_general import SAFETY
from test_routing import data_mock, messages, router_mock


def turn(danger: str, updates: list[ProgressUpdate], work: WorkLog | None = None,
         text: str = "My friend passed out and won't wake up.") -> dict[str, Any]:
    gpt = Mock()
    gpt.create.side_effect = [answer("Stay with them until help arrives."), review()]
    data = data_mock()
    data.search.return_value = {"status": "ok", "dataset_version": "v1", "records": SAFETY}
    return run_turn(messages(text), client=gpt, data=data,
                    model=RELEASE.model, now=NOW,
                    progress=updates.append if work is None else work.watch(updates.append),
                    routing_client=router_mock(route="unresolved", entity="unresolved",
                                               danger=danger),
                    routing_mode="active",
                    diagnostics=None if work is None else {"work": work})


@pytest.mark.parametrize("danger", ["danger", "self_harm"])
def test_the_safety_block_is_sent_as_soon_as_danger_is_read(danger: str) -> None:
    updates: list[ProgressUpdate] = []
    result = turn(danger, updates)
    [first] = [index for index, update in enumerate(updates) if "safety" in update]
    safety = updates[first]["safety"]
    assert updates[first]["stage"] == "understanding"
    # Only the routing step came before it: no lookup, draft or review.
    assert [update["stage"] for update in updates[:first]] == ["understanding"]
    assert first < next(index for index, update in enumerate(updates)
                        if update["stage"] == "reviewing")
    # Exactly what the final answer starts with, links included.
    assert safety["answer"].startswith(SAFETY_NET[danger])
    assert result["answer"].startswith(safety["answer"] + "\n\n")
    assert [citation["id"] for citation in safety["citations"]] == [
        citation["id"] for citation in result["citations"]][:len(safety["citations"])]
    assert safety["citations"] and all(citation["url"].startswith("https://")
                                       for citation in safety["citations"])


def test_no_safety_block_without_danger() -> None:
    updates: list[ProgressUpdate] = []
    result = turn("none", updates, text="I passed out flyers for my club. Where is the Registrar?")
    assert updates and all("safety" not in update for update in updates)
    assert "dangerFrom" not in result["metrics"]["routing"]


@pytest.mark.parametrize("text,kind", [
    # Q30's wording: Jev read "if someone is unconscious" as danger on one run and not
    # the next (09-29 replay).
    ("Tell me the next shuttle, where the Registrar is, and what I should do if someone is "
     "unconscious.", "danger"),
    ("My roommate isn't breathing", "danger"),
    ("I think I want to kill myself", "self_harm"),
])
def test_words_that_name_an_emergency_send_the_block_when_jev_misses_it(
    text: str, kind: str
) -> None:
    updates: list[ProgressUpdate] = []
    result = turn("none", updates, text=text)
    [safety] = [update["safety"] for update in updates if "safety" in update]
    assert safety["answer"].startswith(SAFETY_NET[kind])
    assert result["answer"].startswith(safety["answer"])
    assert result["metrics"]["routing"]["dangerFrom"] == "words"


def test_the_work_log_and_stream_pass_the_block_through_unchanged() -> None:
    updates: list[ProgressUpdate] = []
    turn("danger", updates)
    [update] = [update for update in updates if "safety" in update]
    forwarded: list[ProgressUpdate] = []
    WorkLog(0).watch(forwarded.append)(update)
    assert forwarded == [update]

    async def exercise() -> list[str]:
        gate = asyncio.Event()
        queue: asyncio.Queue[ProgressUpdate] = asyncio.Queue()

        async def work() -> dict[str, object]:
            await gate.wait()
            return {"answer": "final"}

        worker = asyncio.create_task(work())
        stream = stream_turn(worker, queue, Event(), 1,
                             lambda status, reason: JSONResponse({"reason": reason}, status))
        frames = [await anext(stream)]
        queue.put_nowait(update)
        frames.append(await anext(stream))
        gate.set()
        frames.append(await anext(stream))
        await stream.aclose()
        return frames

    frames = asyncio.run(exercise())
    assert frames[1].startswith("event: progress\n")
    sent = json.loads(frames[1].split("data: ", 1)[1])
    assert sent["safety"] == update["safety"]
    assert frames[2].startswith("event: result\n")


@pytest.mark.parametrize("danger", ["danger", "none"])
def test_the_safety_block_adds_no_timeline_step(danger: str) -> None:
    # The block belongs to the routing step, so the Dev Timeline shows no empty second
    # "understanding" on danger turns; the update still reaches the student.
    work = WorkLog(monotonic())
    updates: list[ProgressUpdate] = []
    # Words that name no emergency, so only Jev's pick decides.
    turn(danger, updates, work, text="Where is the Registrar?")
    steps = work.report()["steps"]
    assert [step["stage"] for step in steps] == [
        "connecting", "understanding", "understanding", "reviewing"]
    assert "routing" in steps[1] and "draft" in steps[2]
    assert steps[1].get("safety", False) is (danger != "none")
    assert any("safety" in update for update in updates) is (danger != "none")
