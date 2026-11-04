"""The release the Brain serves, and moving to a newer one only once it is ready.

Before, a new release was installed first and its lists read after, while students asked: on
11-04 each question began its own copy of each list, the office list missed its 3 s limit and
questions fell to the slow AI path (41 s, then time-outs). Now a new release is opened in a
reader of its own, checked, and read in full (meaning index, how-to sections, every list) in an
engine of its own while the engine serving keeps answering. Only then is it installed and the
Brain switched to it; a question already being answered finishes on the engine it began on. A
release that fails to open or pass its checks is never installed or served, and is not tried
again until another one arrives.

Production's Brain gets each release from the file store (GraphFollower). The laptop's copy
changes only when Dan syncs it by hand (run-local.sh sync-prod), and its running Brain moves to
it the same way, from its own folder.
"""

import ctypes
import gc
import logging
import os
import threading
import weakref
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rockygpt_brain.engine import ChatEngine
from rockygpt_brain.retrieval.graph_store import checked_release, read_pointer
from rockygpt_brain.retrieval.graph_sync import GraphFollower, release_key

LOG = logging.getLogger(__name__)
WATCH_SECONDS = 5.0  # How often a Brain without a store looks at its own folder (the laptop).
RETIRE_SECONDS = 120  # How long a replaced engine's last questions may take (a turn is 52 s).


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
    opens and passes its checks, from the store (production) or the folder (the laptop)."""

    def __init__(self, state: Any, directory: Path | str,
                 follower: GraphFollower | None = None) -> None:
        self.state = state
        self.directory = Path(directory)
        self.follower = follower
        self.refused: str | None = None  # The last release that failed to open or check.
        self.replaced: weakref.ref[ChatEngine] | None = None  # The engine the last switch let go.
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
        serving = getattr(self.state.engine.facts, "release", None)
        if key == self.refused or (serving is not None and key == release_key(serving)):
            return False
        try:
            facts = checked_release(self.directory, pointer)
            engine: ChatEngine = self.state.engine.for_release(facts)
            seconds = engine.warm()
        except Exception as error:  # noqa: BLE001 - a release that cannot be read is refused
            self._refuse(pointer, error)
            return False
        if self.follower is not None and not self.follower.install(pointer):
            return False  # Not installed (the disk said no): the next check tries again.
        self.replaced = weakref.ref(self.state.engine)
        self.state.engine = engine
        LOG.info("brain_release_switched release=%s warm_seconds=%s rss_mb=%s",
                 pointer.get("dataset_version"), round(sum(seconds.values()), 2), rss_mb())
        return True

    def give_back(self) -> None:
        """After a switch: once the replaced engine's last question is done and it is gone, hand
        the memory it held back to the system, so the next switch starts from one release."""
        replaced = self.replaced
        for _ in range(RETIRE_SECONDS):
            if replaced is None or replaced() is None or self._stop.wait(1):
                break
        gc.collect()
        before = rss_mb()
        give_back_memory()
        LOG.info("brain_release_memory_given_back replaced_gone=%s rss_mb=%s->%s",
                 replaced is None or replaced() is None, before, rss_mb())

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
                    if self.check_once():
                        self.give_back()
                except Exception:  # noqa: BLE001 - the Brain keeps serving the release it has
                    LOG.exception("brain_release_check_failed")

        self._thread = threading.Thread(target=loop, name="releases", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
