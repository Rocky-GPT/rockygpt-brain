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


class MeaningScores(dict[tuple[str, str], float]):
    """Each unit's similarity to a question; `at` says where in a long unit's text the best
    matching piece starts (absent when its opening or a written question matched best)."""

    at: dict[tuple[str, str], int]


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
        # Where vector k's text starts in its unit's text: 0 the opening, a piece of a long
        # section further in, -1 a question written for the unit.
        self.offsets: list[int] = list(meta.get("offsets") or [0] * len(self.owner))
        # Each office's card (its name, other names and contact titles), one vector each after the
        # units' vectors: an office with no how-to sections (Athletics, a school's dean's office)
        # is still found by what it handles. An index built before cards has none.
        self.cards: list[str] = [str(c) for c in meta.get("cards") or []]
        # Each unit's page address and heading, for picking units by heading (empty before 10-21).
        self.labels: dict[tuple[str, str], tuple[str, str]] = {
            (u.record_id, u.index): (str(label[0]), str(label[1]))
            for u, label in zip(self.units, meta.get("labels") or [], strict=False)}
        # Each program's and course's card (id, kind), after the office cards: a program or course
        # named only in part is found by its meaning. Never office cards: they are read as offices.
        self.academic: list[tuple[str, str]] = [(str(a[0]), str(a[1]))
                                                for a in meta.get("academic") or []]
        # The catalog courses whose published prerequisites name each course, by code.
        self.required_by: dict[str, list[str]] = {
            str(code): [str(c) for c in later]
            for code, later in (meta.get("required_by") or {}).items()}
        # Each course's catalog title by entity id ("Calculus II"), for everyday names.
        titles = {str(k): str(v) for k, v in (meta.get("course_titles") or {}).items()}
        self.course_titles: dict[str, str] = {k: v for k, v in titles.items() if "#" not in k}
        # Each course's Gen Ed attributes by entity id, for "does statistics count for Gen Ed".
        self.course_gen_ed: dict[str, str] = {k.split("#")[0]: v for k, v in titles.items()
                                              if k.endswith("#gen_ed")}
        if len(vectors) != (len(self.owner) + len(self.cards)
                            + len(self.academic)) * self.dimensions:
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

    def scores(self, query: Sequence[float]) -> MeaningScores:
        """Every unit's similarity to the query (its best vector), best first."""
        size = self.dimensions
        if len(query) != size:
            raise ValueError("The query embedding has the wrong size.")
        norm = math.sqrt(dot(query, query)) or 1.0
        unit_query = [value / norm for value in query]
        vectors = self._vectors
        best = [-2.0] * len(self.units)
        where = [0] * len(self.units)
        for k, unit in enumerate(self.owner):
            score = dot(unit_query, vectors[k * size:(k + 1) * size])
            if score > best[unit]:
                best[unit], where[unit] = score, self.offsets[k]
        order = sorted(range(len(best)), key=best.__getitem__, reverse=True)
        scores = MeaningScores(
            ((self.units[u].record_id, self.units[u].index), best[u]) for u in order)
        scores.at = {(self.units[u].record_id, self.units[u].index): where[u]
                     for u in order if where[u] > 0}
        return scores

    def office_scores(self, query: Sequence[float]) -> list[tuple[str, float]]:
        """Every office card's similarity to the query, best first."""
        size = self.dimensions
        if len(query) != size:
            raise ValueError("The query embedding has the wrong size.")
        norm = math.sqrt(dot(query, query)) or 1.0
        unit_query = [value / norm for value in query]
        first = len(self.owner)
        found = [(entity_id, dot(unit_query, self._vectors[(first + k) * size:
                                                           (first + k + 1) * size]))
                 for k, entity_id in enumerate(self.cards)]
        return sorted(found, key=lambda pair: pair[1], reverse=True)

    def academic_scores(self, query: Sequence[float], kind: str) -> list[tuple[str, float]]:
        """Every program's or course's card similarity to the query, best first."""
        size = self.dimensions
        if len(query) != size:
            raise ValueError("The query embedding has the wrong size.")
        norm = math.sqrt(dot(query, query)) or 1.0
        unit_query = [value / norm for value in query]
        first = len(self.owner) + len(self.cards)
        found = [(entity_id, dot(unit_query, self._vectors[(first + k) * size:
                                                           (first + k + 1) * size]))
                 for k, (entity_id, card_kind) in enumerate(self.academic) if card_kind == kind]
        return sorted(found, key=lambda pair: pair[1], reverse=True)

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
