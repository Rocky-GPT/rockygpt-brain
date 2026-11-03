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
import importlib
import json
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

numpy: Any
try:  # Scoring every vector at once; the pure-Python loop is the fallback. Imported by name so
    # the type check never reads numpy's own stubs (written for a newer Python than its target).
    numpy = importlib.import_module("numpy")
except ImportError:  # pragma: no cover - numpy is a dependency; the fallback is for old envs.
    numpy = None


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


# math.sumprod (Python 3.12+) is about twenty times faster than the generator on 43,000 vectors.
dot: Any = getattr(math, "sumprod", _dot)

# The vectors file holds float32 values, DIMENSIONS per vector, each vector of length one.
MEANING_SUFFIX, VECTORS_SUFFIX = ".meaning.json", ".meaning.f32"
MATRIX_CHUNK = 4096  # Vectors widened to float32 at a time (8 MB at 512 dimensions).

# A card found only by its meaning (a club or an event a question names no word of) counts when
# it stands out from the other cards of its kind for that question. Every campus card shares
# much of a campus question's meaning ("Ramapo College student club: ..."), and how much depends
# on the question, so no fixed score tells a match from a crowd: on 11-03 a question about club
# fairs scored 0.47-0.52 against a dozen unrelated events, above the old fixed floor of 0.44,
# while "is there a play i can go see" scored 0.43 against the play itself. The bar is each
# question's own: its median card's score plus STANDOUT spreads, a spread being the cards' median
# distance from that median, scaled to a standard deviation. A big group of matching cards (every
# Greek Life club, for a question about fraternities) barely moves a median, as it would a mean.
# Tried on 86 fresh questions (11-03, each spread estimated there from the question's 90th
# percentile): three spreads dropped every crowd of unrelated clubs and events and kept most of
# those a person would list ("is there a cooking club": Culinary and Pastry Skills; the play). A
# single near card can still pass ("is there a sailing club": S.A.I.L); a reply gives it only as
# the nearest.
STANDOUT, STANDOUT_MIN_CARDS = 3.0, 20
_MAD_TO_SD = 1.4826


def standout_bar(scores: Iterable[float], spreads: float = STANDOUT) -> float | None:
    """The score a card must reach to stand out from all the cards of its kind for one question
    (their median plus `spreads` spreads); None with too few cards to tell."""
    values = sorted(scores)
    if len(values) < STANDOUT_MIN_CARDS:
        return None
    median = statistics.median(values)
    spread = statistics.median(abs(value - median) for value in values) * _MAD_TO_SD
    return median + spreads * spread if spread > 0 else None


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

    def __init__(self, meta: dict[str, Any], vectors: array.array,  # type: ignore[type-arg]
                 scales: array.array | None = None) -> None:  # type: ignore[type-arg]
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
        # The courses whose attributes mark them Writing Intensive, by entity id.
        self.course_writing_intensive: frozenset[str] = frozenset(
            k.split("#")[0] for k in titles if k.endswith("#writing_intensive"))
        # Each event's and club's kinds (kinds.py: "arts", "culture"), by entity id, given once
        # when the index is built. An index built before kinds has none, and then no question
        # is read as asking for a kind.
        self.kinds: dict[str, tuple[str, ...]] = {
            str(entity_id): tuple(str(k) for k in keys)
            for entity_id, keys in (meta.get("kinds") or {}).items() if keys}
        count = len(self.owner) + len(self.cards) + len(self.academic)
        if len(vectors) != count * self.dimensions or (scales is not None
                                                       and len(scales) != count):
            raise ValueError("The meaning index vectors do not match its units.")
        self._vectors = memoryview(vectors)
        # The same vectors as one matrix, when numpy is installed: Render's free CPU took
        # 0.6-1.5 s for each in-process step, so the 47,000 dot products are done at once.
        self._matrix = (numpy.frombuffer(vectors, dtype=numpy.int8 if scales is not None
                                         else numpy.float32).reshape(count, self.dimensions)
                        if numpy is not None and count else None)
        self._matrix_scales = (numpy.frombuffer(scales, dtype=numpy.float32)
                               if numpy is not None and scales is not None and count else None)
        # An index stored in 8 bits (quantize()) keeps each vector's scale: its score is the dot
        # product with the stored integers times that scale.
        self._scales = scales

    def _all_scores(self, unit_query: Sequence[float]) -> Any:
        """Every vector's score as one array (numpy), in chunks so an 8-bit index is never
        widened whole; None without numpy."""
        if self._matrix is None:
            return None
        query = numpy.asarray(unit_query, dtype=numpy.float32)
        out = numpy.empty(len(self._matrix), dtype=numpy.float32)
        for start in range(0, len(self._matrix), MATRIX_CHUNK):
            chunk = self._matrix[start:start + MATRIX_CHUNK]
            out[start:start + len(chunk)] = chunk.astype(numpy.float32) @ query
        if self._matrix_scales is not None:
            out *= self._matrix_scales
        return out

    def _score(self, unit_query: Sequence[float], k: int) -> float:
        size = self.dimensions
        score = float(dot(unit_query, self._vectors[k * size:(k + 1) * size]))
        return score * self._scales[k] if self._scales is not None else score

    @classmethod
    def from_quantized(cls, meta: dict[str, Any], vectors: bytes,
                       scales: bytes) -> "MeaningIndex":
        """An index stored by quantize(): 8-bit vectors and one float32 scale per vector (on 400
        written questions it kept 99.5% of the float index's ten best units, at a quarter of the
        size, 11-01)."""
        stored = array.array("b")
        stored.frombytes(vectors)
        each = array.array("f")
        each.frombytes(scales)
        return cls(meta, stored, each)

    def quantize(self) -> tuple[bytes, bytes]:
        """The vectors in 8 bits, each scaled by its largest value, and the scales."""
        if self._scales is not None:
            return self._vectors.tobytes(), self._scales.tobytes()
        size = self.dimensions
        stored, scales = array.array("b"), array.array("f")
        for k in range(len(self._vectors) // size):
            vector = self._vectors[k * size:(k + 1) * size]
            scale = (max(abs(v) for v in vector) / 127.0) or 1.0
            stored.extend(max(-127, min(127, round(v / scale))) for v in vector)
            scales.append(scale)
        return stored.tobytes(), scales.tobytes()

    @classmethod
    def load(cls, release_file: Path) -> "MeaningIndex | None":
        """The index published beside a release graph file, or None when there is none.

        Its float vectors are kept in 8 bits, exactly as quantize() stores them, read and turned
        into 8 bits MATRIX_CHUNK vectors at a time. Render gives the Brain 512 MB, and since 11-03
        every page section also has three written questions: about 51,000 vectors, 104 MB as
        floats and 26 MB in 8 bits. On the 11-03 index, 8 bits kept the same best unit for 298 of
        300 test questions and 99.3% of the ten best units, and scored faster."""
        meta_path = release_file.with_suffix(MEANING_SUFFIX)
        vectors_path = release_file.with_suffix(VECTORS_SUFFIX)
        if not meta_path.is_file() or not vectors_path.is_file():
            return None
        meta = json.loads(meta_path.read_text())
        if numpy is None:
            vectors = array.array("f")
            vectors.frombytes(vectors_path.read_bytes())
            return cls(meta, vectors)
        size = int(meta["dimensions"])
        stored, scales = array.array("b"), array.array("f")
        with vectors_path.open("rb") as file:
            while len(floats := numpy.fromfile(file, dtype=numpy.float32,
                                               count=MATRIX_CHUNK * size)):
                # quantize()'s own arithmetic (float64, ties to even), so the index loaded here
                # and the one the database stores are the same bytes.
                chunk = floats.astype(numpy.float64).reshape(-1, size)
                scale = numpy.abs(chunk).max(axis=1) / 127.0
                scale[scale == 0] = 1.0
                stored.frombytes(numpy.clip(numpy.rint(chunk / scale[:, None]), -127, 127)
                                 .astype(numpy.int8).tobytes())
                scales.frombytes(scale.astype(numpy.float32).tobytes())
        return cls(meta, stored, scales)

    def scores(self, query: Sequence[float]) -> MeaningScores:
        """Every unit's similarity to the query (its best vector), best first."""
        size = self.dimensions
        if len(query) != size:
            raise ValueError("The query embedding has the wrong size.")
        norm = math.sqrt(dot(query, query)) or 1.0
        unit_query = [value / norm for value in query]
        best = [-2.0] * len(self.units)
        where = [0] * len(self.units)
        every = self._all_scores(unit_query)
        if every is not None and self.owner:
            owner = numpy.asarray(self.owner)
            values = every[:len(owner)]
            # Each unit's best vector: sorted by unit, then best first; the first of each unit.
            order = numpy.lexsort((-values, owner))
            units, first = numpy.unique(owner[order], return_index=True)
            for unit, k in zip(units.tolist(), order[first].tolist(), strict=True):
                best[unit], where[unit] = float(values[k]), self.offsets[k]
        else:
            for k, unit in enumerate(self.owner):
                score = self._score(unit_query, k)
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
        every = self._all_scores(unit_query)
        found = [(entity_id, float(every[first + k]) if every is not None
                  else self._score(unit_query, first + k))
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
        every = self._all_scores(unit_query)
        found = [(entity_id, float(every[first + k]) if every is not None
                  else self._score(unit_query, first + k))
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
