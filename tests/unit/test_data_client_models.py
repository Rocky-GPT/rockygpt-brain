import pytest

from rockygpt_brain.data_client.errors import DataContractError
from rockygpt_brain.data_client.models import (
    MAX_SOURCE_ID_LENGTH,
    Dataset,
    SafetyResources,
    SearchResult,
    Source,
    normalize_source,
)


class TestNormalizeSource:
    def test_valid_source_passes_through(self) -> None:
        source = Source(source_id="abc-123", title="Registrar", url="https://example.edu/registrar")
        normalized = normalize_source(source)
        assert normalized is not None
        assert normalized.source_id == "abc-123"
        assert normalized.url == "https://example.edu/registrar"

    def test_oversized_source_id_rejected(self) -> None:
        source = Source(source_id="x" * (MAX_SOURCE_ID_LENGTH + 1), title="T", url="https://example.edu/a")
        assert normalize_source(source) is None

    def test_non_http_scheme_rejected(self) -> None:
        source = Source(source_id="id1", title="T", url="javascript:alert(1)")
        assert normalize_source(source) is None

    def test_relative_url_rejected(self) -> None:
        source = Source(source_id="id1", title="T", url="/relative/path")
        assert normalize_source(source) is None

    def test_leading_whitespace_on_source_id_rejected_not_trimmed(self) -> None:
        source = Source(source_id=" id1", title="T", url="https://example.edu/a")
        assert normalize_source(source) is None

    def test_trailing_whitespace_on_url_rejected_not_trimmed(self) -> None:
        source = Source(source_id="id1", title="T", url="https://example.edu/a ")
        assert normalize_source(source) is None

    def test_control_char_in_source_id_rejected(self) -> None:
        source = Source(source_id="id1\x00", title="T", url="https://example.edu/a")
        assert normalize_source(source) is None

    def test_bidi_override_in_title_rejected(self) -> None:
        source = Source(source_id="id1", title="Title‮evil", url="https://example.edu/a")
        assert normalize_source(source) is None

    def test_title_is_trimmed_safely(self) -> None:
        source = Source(source_id="id1", title="  spaced title  ", url="https://example.edu/a")
        normalized = normalize_source(source)
        assert normalized is not None
        assert normalized.title == "spaced title"

    def test_whitespace_only_title_rejected(self) -> None:
        source = Source(source_id="id1", title="   ", url="https://example.edu/a")
        assert normalize_source(source) is None

    def test_empty_fields_rejected(self) -> None:
        blank_id = Source(source_id="", title="T", url="https://example.edu/a")
        blank_title = Source(source_id="id1", title="", url="https://example.edu/a")
        blank_url = Source(source_id="id1", title="T", url="")
        assert normalize_source(blank_id) is None
        assert normalize_source(blank_title) is None
        assert normalize_source(blank_url) is None


class TestStrictFromJson:
    def test_source_rejects_unknown_field(self) -> None:
        with pytest.raises(DataContractError):
            Source.from_json({"sourceId": "a", "title": "b", "url": "https://x.edu", "extra": 1})

    def test_source_rejects_wrong_type(self) -> None:
        with pytest.raises(DataContractError):
            Source.from_json({"sourceId": 123, "title": "b", "url": "https://x.edu"})

    def test_source_rejects_non_object(self) -> None:
        with pytest.raises(DataContractError):
            Source.from_json(["not", "an", "object"])

    def test_source_rejects_missing_field(self) -> None:
        with pytest.raises(DataContractError):
            Source.from_json({"sourceId": "a", "title": "b"})

    def test_source_rejects_naive_collected_at(self) -> None:
        with pytest.raises(DataContractError):
            Source.from_json(
                {
                    "sourceId": "a",
                    "title": "b",
                    "url": "https://x.edu",
                    "collectedAt": "2024-01-01T00:00:00",
                }
            )

    def test_source_accepts_tz_aware_collected_at(self) -> None:
        result = Source.from_json(
            {
                "sourceId": "a",
                "title": "b",
                "url": "https://x.edu",
                "collectedAt": "2024-01-01T00:00:00Z",
            }
        )
        assert result.source_id == "a"

    def test_dataset_requires_exact_keys(self) -> None:
        with pytest.raises(DataContractError):
            Dataset.from_json({"id": "a", "version": "1"})  # missing activatedAt

    def test_dataset_valid(self) -> None:
        dataset = Dataset.from_json(
            {"id": "a", "version": "1", "activatedAt": "2024-01-01T00:00:00Z"}
        )
        assert dataset.id == "a"

    def test_search_result_rejects_non_list_records(self) -> None:
        with pytest.raises(DataContractError):
            SearchResult.from_json(
                {
                    "dataset": {"id": "a", "version": "1", "activatedAt": "2024-01-01T00:00:00Z"},
                    "records": "not-a-list",
                }
            )

    def test_search_result_rejects_non_object_record(self) -> None:
        with pytest.raises(DataContractError):
            SearchResult.from_json(
                {
                    "dataset": {"id": "a", "version": "1", "activatedAt": "2024-01-01T00:00:00Z"},
                    "records": ["not-an-object"],
                }
            )

    def test_search_result_valid(self) -> None:
        result = SearchResult.from_json(
            {
                "dataset": {"id": "a", "version": "1", "activatedAt": "2024-01-01T00:00:00Z"},
                "records": [{"name": "x"}],
            }
        )
        assert result.records == [{"name": "x"}]

    def test_safety_resources_rejects_extra_top_level_field(self) -> None:
        valid_source = {"sourceId": "s", "title": "T", "url": "https://x.edu"}
        with pytest.raises(DataContractError):
            SafetyResources.from_json(
                {
                    "dataset": {"id": "a", "version": "1", "activatedAt": "2024-01-01T00:00:00Z"},
                    "emergencyPhone": "911",
                    "sources": {"safety": valid_source, "counseling": valid_source},
                    "extra": True,
                }
            )

    def test_safety_resources_valid(self) -> None:
        valid_source = {"sourceId": "s", "title": "T", "url": "https://x.edu"}
        result = SafetyResources.from_json(
            {
                "dataset": {"id": "a", "version": "1", "activatedAt": "2024-01-01T00:00:00Z"},
                "emergencyPhone": "911",
                "sources": {"safety": valid_source, "counseling": valid_source},
            }
        )
        assert result.emergency_phone == "911"
