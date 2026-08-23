from datetime import UTC, datetime

from rockygpt_brain.brain.grounding import MAX_CITED_SOURCE_IDS, ProvenanceRegistry
from rockygpt_brain.data_client.models import Source


def _source(source_id: str = "src-1", **overrides: object) -> Source:
    defaults: dict[str, object] = {
        "source_id": source_id,
        "title": "Registrar",
        "url": "https://example.edu/registrar",
        "collected_at": datetime(2024, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Source(**defaults)  # type: ignore[arg-type]


def test_resolve_known_id_returns_citation() -> None:
    registry = ProvenanceRegistry()
    registry.record([_source("src-1")])
    citations = registry.resolve(["src-1"])
    assert citations is not None
    assert len(citations) == 1
    assert citations[0].source_id == "src-1"
    assert citations[0].url == "https://example.edu/registrar"


def test_resolve_fails_closed_on_unknown_id() -> None:
    registry = ProvenanceRegistry()
    registry.record([_source("src-1")])
    # A citedSourceId this turn's tools never produced must fail the whole
    # batch (None), not silently return a shorter list.
    assert registry.resolve(["src-1", "src-unknown"]) is None


def test_resolve_empty_list_is_empty_citations_not_none() -> None:
    registry = ProvenanceRegistry()
    registry.record([_source("src-1")])
    assert registry.resolve([]) == []


def test_resolve_over_cap_fails_closed() -> None:
    registry = ProvenanceRegistry()
    ids = [f"src-{i}" for i in range(MAX_CITED_SOURCE_IDS + 1)]
    registry.record([_source(source_id=i) for i in ids])
    assert registry.resolve(ids) is None


def test_record_ignores_invalid_sources() -> None:
    registry = ProvenanceRegistry()
    registry.record([_source("bad", url="javascript:alert(1)")])
    assert registry.resolve(["bad"]) is None
    assert registry.known_source_ids() == []


def test_record_is_first_write_wins_for_duplicate_ids() -> None:
    registry = ProvenanceRegistry()
    registry.record([_source("src-1", title="First")])
    registry.record([_source("src-1", title="Second")])
    citations = registry.resolve(["src-1"])
    assert citations is not None
    assert citations[0].title == "First"


def test_model_visible_id_matches_registry_key_exactly() -> None:
    # The same normalize_source pass that decides the model-visible
    # sourceId (brain/tools.py) also governs what the registry stores it
    # under, so a source that survives normalization is citable under
    # exactly the id a caller would see.
    registry = ProvenanceRegistry()
    raw = _source("  padded-would-be-rejected  ")
    registry.record([raw])
    assert registry.known_source_ids() == []

    clean = _source("clean-id")
    registry.record([clean])
    assert registry.known_source_ids() == ["clean-id"]
    citations = registry.resolve(["clean-id"])
    assert citations is not None and citations[0].source_id == "clean-id"
