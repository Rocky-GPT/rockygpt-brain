"""Per-turn citation provenance.

DESIGN.md §4: the model is never trusted to author a citation's title/url —
it may only *select* a `sourceId` it saw in a tool result during this turn.
This registry is the only place a `Citation` object is constructed, and it
only ever builds one from a `Source` that both passed `data_client.models
.normalize_source` and appeared in a real data-service response this turn.

`resolve` is a **strict, all-or-nothing** lookup: if every requested
`sourceId` is a known, currently-registered id, it returns the matching
citations in order; if *any* requested id is unknown, it returns `None` for
the whole batch rather than silently returning a partial list. A model that
references a `sourceId` this turn's tools never actually produced is either
buggy or has been manipulated — orchestrator.py treats a `None` result as a
reason to fall back to a safe default answer, not as "mostly grounded, drop
the bad one and proceed," which would let a partially-fabricated-looking
answer through unnoticed. `orchestrator.py`'s own duplicate/format
validation on the submitted list (brain/answer.py) means `resolve` never
needs to deduplicate.

`brain/tools.py` calls the same `normalize_source` function on the same
input when deciding what `sourceId` to expose to the model in the first
place, so the id the model sees is *exactly* the id this registry stores
it under — there is no second, independently-tuned validation step here
that could disagree and cause a real, model-visible id to fail to resolve
(or a rejected one to somehow resolve).
"""

from __future__ import annotations

from rockygpt_brain.data_client.models import Source, normalize_source
from rockygpt_brain.schemas.common import Citation

MAX_CITED_SOURCE_IDS = 32


class ProvenanceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}

    def record(self, sources: list[Source]) -> None:
        for source in sources:
            normalized = normalize_source(source)
            if normalized is None:
                continue
            self._sources.setdefault(normalized.source_id, normalized)

    def is_known(self, source_id: str) -> bool:
        return source_id in self._sources

    def resolve(self, cited_source_ids: list[str]) -> list[Citation] | None:
        """Strict resolution: `None` if any id is unknown or the list is
        over `MAX_CITED_SOURCE_IDS`, otherwise every citation, in order."""
        if len(cited_source_ids) > MAX_CITED_SOURCE_IDS:
            return None
        citations: list[Citation] = []
        for source_id in cited_source_ids:
            source = self._sources.get(source_id)
            if source is None:
                return None
            citations.append(
                Citation(
                    source_id=source.source_id,
                    title=source.title,
                    url=source.url,
                    collected_at=source.collected_at,
                )
            )
        return citations

    def known_source_ids(self) -> list[str]:
        return list(self._sources.keys())
