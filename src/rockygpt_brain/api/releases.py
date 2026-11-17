"""The release the Brain serves, and moving to a newer one only once it is ready.

Before 11-04 a new release was installed first and its lists read after, while students asked:
each question began its own copy of each list, the office list missed its 3 s limit and questions
fell to the slow AI path (41 s, then time-outs). From 11-04 a new release was read in full in an
engine of its own while the old engine kept answering, so for about a minute the Brain held two
releases. On 11-17, in a box the size of Render's (512 MB, half a processor), the dev graph read
that way was killed for memory 4 times out of 4.

Now a switch pauses instead (pause and swap): new questions wait at the gate, the answers in flight
finish, the old engine is dropped and its memory given back, and only then is the new release
opened, checked and read in full in an engine of its own. In the same box that held at most
378 MB, 0 kills in 3, and new questions waited 11 to 21 s once per release (most nights a release
arrives before students are up). A release that fails to open or pass its checks is never
installed: the Brain reads the release it served again and is back on it, and does not try the
refused one again until another arrives. If the answers in flight do not finish within
PAUSE_LIMIT_SECONDS the switch is put off to the next check and nobody waits longer.

Production's Brain gets each release from the file store (GraphFollower). The laptop's copy
changes only when Dan syncs it by hand (run-local.sh sync-prod), and its running Brain moves to
it the same way, from its own folder.
"""

import asyncio
import ctypes
import gc
import logging
import os
import threading
import time
import weakref
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval.entity_facts import EntityFacts
from rockygpt_brain.retrieval.graph_store import checked_release, read_pointer
from rockygpt_brain.retrieval.graph_sync import GraphFollower, release_key

LOG = logging.getLogger(__name__)
WATCH_SECONDS = 5.0  # How often a Brain without a store looks at its own folder (the laptop).
PAUSE_LIMIT_SECONDS = 60.0  # How long a switch waits for the answers in flight (a turn is 52 s).
GONE_SECONDS = 5.0  # How long a switch waits for the dropped engine to be collected.
GATE_POLL_SECONDS = 0.05  # How often a question waiting at a closed gate looks again.


class Gate:
    """Where each question passes on its way to the engine. A release switch closes it: questions
    already past it finish, new ones wait until it opens again. Shared by the server's event loop
    (questions) and the releases thread (the switch)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._busy = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @asynccontextmanager
    async def passing(self) -> AsyncIterator[None]:
        """Hold a place past the gate for one question (or any read of the engine)."""
        while True:
            with self._lock:
                if not self._closed:
                    self._busy += 1
                    break
            await asyncio.sleep(GATE_POLL_SECONDS)
        try:
            yield
        finally:
            with self._lock:
                self._busy -= 1
                if not self._busy:
                    self._idle.notify_all()

    @contextmanager
    def passing_now(self) -> Iterator[bool]:
        """A place past the gate without waiting: yields True when the gate was open (the place is
        held until the block ends), False during a switch. For the emergency floor, which must
        never wait: during a switch it gives its fixed emergency text alone."""
        with self._lock:
            passed = not self._closed
            if passed:
                self._busy += 1
        try:
            yield passed
        finally:
            if passed:
                with self._lock:
                    self._busy -= 1
                    if not self._busy:
                        self._idle.notify_all()

    def close(self, timeout: float) -> bool:
        """Stop new questions, then wait up to timeout for the ones past the gate to finish. True
        when none is left; False (and the gate open again) when some still were."""
        with self._lock:
            self._closed = True
            if self._idle.wait_for(lambda: not self._busy, timeout):
                return True
            self._closed = False
            return False

    def open(self) -> None:
        with self._lock:
            self._closed = False


def give_back_memory() -> None:
    """Hand the memory the C allocator holds free back to the system (glibc's malloc_trim). It
    keeps it otherwise: after a switch in the cloud 319 MB stayed where 217 MB were in use, and
    the next switch would start from there (Render gives the Brain 512 MB). Nothing to do where
    there is no malloc_trim, as on macOS."""
    try:
        ctypes.CDLL(None).malloc_trim(0)
    except (OSError, AttributeError):
        pass


def rss_mb() -> float | None:
    """The memory this process holds now, in MB, where the system says (Linux)."""
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return round(pages * os.sysconf("SC_PAGE_SIZE") / 2**20, 1)


class Releases:
    """Keep state.engine (the engine that answers the next question) on the newest release that
    opens and passes its checks, from the store (production) or the folder (the laptop).
    state.gate, made here when the state has none, is where questions wait during a switch."""

    def __init__(self, state: Any, directory: Path | str,
                 follower: GraphFollower | None = None) -> None:
        self.state = state
        if getattr(state, "gate", None) is None:
            state.gate = Gate()
        self.gate: Gate = state.gate
        self.directory = Path(directory)
        self.follower = follower
        self.refused: str | None = None  # The last release that failed to open or check.
        # Builds an engine with the serving engine's settings for other facts, without holding
        # that engine (or its release) itself.
        self._make: Callable[[EntityFacts], ChatEngine] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def first(self) -> None:
        """Production, before the server takes questions: download the store's release and
        install it once it opens and passes its checks. One that fails is refused, and the copy
        already here (if any) is served instead."""
        if self.follower is None:
            return
        pointer = self.follower.fetch()
        if pointer is None:
            return
        try:
            checked_release(self.directory, pointer)
        except Exception as error:  # noqa: BLE001 - a release that cannot be read is refused
            self._refuse(pointer, error)
            return
        self.follower.install(pointer)

    def check_once(self) -> bool:
        """One look for a newer release; True when the Brain switched to one."""
        pointer = (self.follower.fetch(skip=self.refused) if self.follower is not None
                   else read_pointer(self.directory))
        if pointer is None:
            return False
        key = release_key(pointer)
        current = self.state.engine
        if current is not None:
            self._make = current.blueprint()
        if self._make is None:
            return False  # No engine has served yet: nothing to build a new one like.
        serving = getattr(getattr(current, "facts", None), "release", None)
        del current
        if key == self.refused or (serving is not None and key == release_key(serving)):
            return False
        return self._swap(pointer, serving)

    def _swap(self, pointer: Mapping[str, Any], serving: Mapping[str, Any] | None) -> bool:
        """Pause and swap: hold new questions, let the answers in flight finish, drop the old
        engine, read the new release in full, serve it, let the questions in."""
        assert self._make is not None
        paused = time.monotonic()
        if not self.gate.close(PAUSE_LIMIT_SECONDS):
            LOG.warning("brain_release_switch_put_off release=%s reason=answers_in_flight "
                        "waited_seconds=%s", pointer.get("dataset_version"), PAUSE_LIMIT_SECONDS)
            return False
        drained = round(time.monotonic() - paused, 2)
        try:
            self._drop_engine()
            low = rss_mb()
            try:
                engine = self._ready(pointer)
            except Exception as error:  # noqa: BLE001 - a release that cannot be read is refused
                self._refuse(pointer, error)
                self._back_to(serving)
                return False
            if self.follower is not None and not self.follower.install(pointer):
                # Read and checked in full, so it serves; the folder's pointer still names the
                # old one, and a restart serves that until the next check installs this one.
                LOG.warning("brain_release_not_installed release=%s",
                            pointer.get("dataset_version"))
            self.state.engine = engine
            del engine
            give_back_memory()
            LOG.info("brain_release_switched release=%s questions_waited_seconds=%s "
                     "answers_in_flight_seconds=%s rss_mb_lowest=%s rss_mb=%s",
                     pointer.get("dataset_version"), round(time.monotonic() - paused, 2),
                     drained, low, rss_mb())
            return True
        finally:
            self.gate.open()

    def _ready(self, pointer: Mapping[str, Any]) -> ChatEngine:
        """An engine on pointer's release, opened, checked and read in full."""
        assert self._make is not None
        facts = checked_release(self.directory, pointer)
        engine = self._make(facts)
        del facts
        engine.warm()
        return engine

    def _drop_engine(self) -> None:
        """Let the serving engine go and hand its memory back before another release is read."""
        old = self.state.engine
        gone = weakref.ref(old) if old is not None else None
        self.state.engine = None
        del old
        deadline = time.monotonic() + GONE_SECONDS
        while gone is not None and gone() is not None and time.monotonic() < deadline:
            gc.collect()
            if gone() is not None:
                time.sleep(0.05)
        gc.collect()
        give_back_memory()
        if gone is not None and gone() is not None:
            LOG.warning("brain_release_old_engine_still_held")

    def _back_to(self, serving: Mapping[str, Any] | None) -> None:
        """After a refused release: serve the release served before it again."""
        if serving is None:
            LOG.error("brain_release_restore_failed reason=no_release_named")
            return
        try:
            self.state.engine = self._ready(serving)
        except Exception as error:  # noqa: BLE001 - logged; the next check tries again
            LOG.error("brain_release_restore_failed release=%s exception_type=%s",
                      serving.get("dataset_version"), type(error).__name__)

    def _refuse(self, pointer: Mapping[str, Any], error: Exception) -> None:
        self.refused = release_key(pointer)
        LOG.warning("brain_release_refused release=%s exception_type=%s reason=%s",
                    pointer.get("dataset_version"), type(error).__name__, str(error)[:200])

    def wait(self) -> float:
        return self.follower.wait() if self.follower is not None else WATCH_SECONDS

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self.wait()):
                try:
                    self.check_once()
                except Exception:  # noqa: BLE001 - the Brain keeps serving the release it has
                    LOG.exception("brain_release_check_failed")

        self._thread = threading.Thread(target=loop, name="releases", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
