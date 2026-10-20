"""Search how-to units by meaning, not by shared words.

A student asks "can I get excused for a funeral"; the page says "course attendance is determined
by the instructor". No word is shared, so word matching never offers it. Every unit a how-to read
can offer (an office page's how-to entry or headed section, keyed by its record and its index in
the page, exactly as `_procedure_units` numbers them) has an embedding of its page, heading and
text, and of a few questions a student might type for it. A question's embedding is compared with
all of them; a unit scores its best match.

The index is built beside a published release graph (scripts/build_meaning_index.py) and names the
release it was built from; it is only used for that release. Embeddings find units; they are never
evidence: what a unit says still comes from the release's own records.
"""

import array
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


# math.sumprod (Python 3.12+) is about twenty times faster than the generator on 43,000 vectors.
dot: Any = getattr(math, "sumprod", _dot)

# The vectors file holds float32 values, DIMENSIONS per vector, each vector of length one.
MEANING_SUFFIX, VECTORS_SUFFIX = ".meaning.json", ".meaning.f32"


@dataclass(frozen=True)
class MeaningUnit:
    record_id: str
    index: str
    entity_ids: tuple[str, ...]


class MeaningIndex:
    """The unit embeddings of one release, searched by cosine similarity."""

    def __init__(self, meta: dict[str, Any], vectors: array.array) -> None:  # type: ignore[type-arg]
        self.dataset_version: str = meta["dataset_version"]
        self.identity_hash: str = meta["identity_hash"]
        self.model: str = meta["model"]
        self.dimensions: int = int(meta["dimensions"])
        self.units = [MeaningUnit(str(u[0]), str(u[1]), tuple(u[2])) for u in meta["units"]]
        # Each office's published name, by entity id, for the offices a search names.
        self.names: dict[str, str] = dict(meta.get("entities", {}))
        # Vector k belongs to unit owner[k]: a unit's own text and its written questions.
        self.owner: list[int] = list(meta["owner"])
        if len(vectors) != len(self.owner) * self.dimensions:
            raise ValueError("The meaning index vectors do not match its units.")
        self._vectors = memoryview(vectors)

    @classmethod
    def load(cls, release_file: Path) -> "MeaningIndex | None":
        """The index published beside a release graph file, or None when there is none."""
        meta_path = release_file.with_suffix(MEANING_SUFFIX)
        vectors_path = release_file.with_suffix(VECTORS_SUFFIX)
        if not meta_path.is_file() or not vectors_path.is_file():
            return None
        vectors = array.array("f")
        vectors.frombytes(vectors_path.read_bytes())
        return cls(json.loads(meta_path.read_text()), vectors)

    def scores(self, query: Sequence[float]) -> dict[tuple[str, str], float]:
        """Every unit's similarity to the query (its best vector), best first."""
        size = self.dimensions
        if len(query) != size:
            raise ValueError("The query embedding has the wrong size.")
        norm = math.sqrt(dot(query, query)) or 1.0
        unit_query = [value / norm for value in query]
        vectors = self._vectors
        best = [-2.0] * len(self.units)
        for k, unit in enumerate(self.owner):
            score = dot(unit_query, vectors[k * size:(k + 1) * size])
            if score > best[unit]:
                best[unit] = score
        order = sorted(range(len(best)), key=best.__getitem__, reverse=True)
        return {(self.units[u].record_id, self.units[u].index): best[u] for u in order}

    def offices(self, ranked: Mapping[tuple[str, str], float], *, exclude: Sequence[str] = (),
                exclude_records: Sequence[str] = (), limit: int = 3) -> list[str]:
        """The offices whose own units rank best, best first: each office counts by its best unit,
        and a page an excluded office links is not another office's."""
        by_key = {(u.record_id, u.index): u for u in self.units}
        skip, skip_records, found = set(exclude), set(exclude_records), []
        for key in ranked:
            unit = by_key.get(key)
            if unit is None or unit.record_id in skip_records or skip & set(unit.entity_ids):
                continue
            for entity_id in unit.entity_ids:
                if entity_id not in found:
                    found.append(entity_id)
            if len(found) >= limit:
                break
        return found[:limit]
