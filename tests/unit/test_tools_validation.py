from datetime import UTC, datetime

from rockygpt_brain.brain.grounding import ProvenanceRegistry
from rockygpt_brain.brain.time_context import resolve_time_context
from rockygpt_brain.brain.tools import (
    MAX_RECORDS_PER_CALL,
    TOOL_HANDLERS,
    _bound_value,
    _summarize,
    _validate_arguments,
    execute_tool,
)
from rockygpt_brain.data_client.models import Dataset, SearchResult

_SEARCH_CAMPUS_HOURS = TOOL_HANDLERS["search_campus_hours"]
_SEARCH_SHUTTLES = TOOL_HANDLERS["search_shuttles"]


class TestValidateArguments:
    def test_valid_arguments_pass(self) -> None:
        result = _validate_arguments(_SEARCH_CAMPUS_HOURS, {"q": "library", "day": "Monday"})
        assert result == {"q": "library", "day": "Monday"}

    def test_empty_arguments_ok(self) -> None:
        assert _validate_arguments(_SEARCH_CAMPUS_HOURS, {}) == {}

    def test_non_dict_rejected(self) -> None:
        assert _validate_arguments(_SEARCH_CAMPUS_HOURS, None) is None
        assert _validate_arguments(_SEARCH_CAMPUS_HOURS, ["q", "library"]) is None
        assert _validate_arguments(_SEARCH_CAMPUS_HOURS, "library") is None

    def test_unknown_key_rejected(self) -> None:
        assert _validate_arguments(_SEARCH_CAMPUS_HOURS, {"q": "library", "evil": "x"}) is None

    def test_wrong_type_value_rejected(self) -> None:
        assert _validate_arguments(_SEARCH_CAMPUS_HOURS, {"q": 123}) is None

    def test_oversized_value_rejected(self) -> None:
        assert _validate_arguments(_SEARCH_CAMPUS_HOURS, {"q": "x" * 201}) is None

    def test_enum_violation_rejected(self) -> None:
        assert _validate_arguments(_SEARCH_CAMPUS_HOURS, {"day": "Someday"}) is None

    def test_valid_enum_value_passes(self) -> None:
        assert _validate_arguments(_SEARCH_SHUTTLES, {"serviceDay": "weekday"}) == {
            "serviceDay": "weekday"
        }


class TestExecuteTool:
    async def test_unknown_tool_returns_fixed_error(self) -> None:
        registry = ProvenanceRegistry()
        time_context = resolve_time_context(now=None, timezone_name=None)
        result = await execute_tool(
            "not_a_real_tool", {}, client=None, time_context=time_context, registry=registry
        )
        assert result == {"error": "unknown_tool"}

    async def test_invalid_arguments_returns_fixed_error(self) -> None:
        registry = ProvenanceRegistry()
        time_context = resolve_time_context(now=None, timezone_name=None)
        result = await execute_tool(
            "search_campus_hours",
            {"unexpected": "value"},
            client=None,
            time_context=time_context,
            registry=registry,
        )
        assert result == {"error": "invalid_arguments"}


def _record(source_id: str, **fields: object) -> dict[str, object]:
    # Defaults first, then `fields` applied on top, so a caller-supplied
    # `source=...` override actually takes effect (a dict literal with
    # `**fields` placed *before* a duplicate literal key would instead let
    # the literal key silently win, defeating any override).
    record: dict[str, object] = {
        "name": "Some Office",
        "source": {
            "sourceId": source_id,
            "title": f"Title {source_id}",
            "url": f"https://example.edu/{source_id}",
        },
    }
    record.update(fields)
    return record


class TestSummarize:
    def test_records_beyond_cap_are_not_citable(self) -> None:
        registry = ProvenanceRegistry()
        records = [_record(f"src-{i}") for i in range(MAX_RECORDS_PER_CALL + 3)]
        result = SearchResult(
            dataset=Dataset(id="d1", version="v1", activated_at=datetime(2024, 1, 1, tzinfo=UTC)),
            records=records,
        )
        envelope = _summarize(result, registry=registry)
        assert len(envelope["records"]) <= MAX_RECORDS_PER_CALL
        # The extra records past the cap must never become citable.
        assert registry.resolve([f"src-{MAX_RECORDS_PER_CALL + 2}"]) is None

    def test_shown_record_source_id_matches_registry(self) -> None:
        registry = ProvenanceRegistry()
        result = SearchResult(
            dataset=Dataset(id="d1", version="v1", activated_at=datetime(2024, 1, 1, tzinfo=UTC)),
            records=[_record("src-1")],
        )
        envelope = _summarize(result, registry=registry)
        shown_id = envelope["records"][0]["sourceId"]
        assert registry.resolve([shown_id]) is not None

    def test_invalid_source_omits_source_id_and_is_not_citable(self) -> None:
        # A malformed source (bad URL scheme here) must be dropped by
        # normalize_source: no `sourceId` shown to the model, and nothing
        # registered as citable. If normalization were ever bypassed, the
        # invalid `"sourceId": "s"` below would leak into the summary and
        # this assertion would fail.
        registry = ProvenanceRegistry()
        result = SearchResult(
            dataset=Dataset(id="d1", version="v1", activated_at=datetime(2024, 1, 1, tzinfo=UTC)),
            records=[
                _record(
                    "src-1", source={"sourceId": "s", "title": "T", "url": "javascript:x"}
                )
            ],
        )
        envelope = _summarize(result, registry=registry)
        assert "sourceId" not in envelope["records"][0]
        assert registry.resolve(["s"]) is None

    def test_non_finite_float_becomes_none(self) -> None:
        assert _bound_value(float("nan")) is None
        assert _bound_value(float("inf")) is None

    def test_string_bounded(self) -> None:
        from rockygpt_brain.brain.tools import MAX_STRING_LENGTH

        bounded = _bound_value("x" * (MAX_STRING_LENGTH + 100))
        assert len(bounded) == MAX_STRING_LENGTH

    def test_deeply_nested_value_bounded(self) -> None:
        from rockygpt_brain.brain.tools import MAX_DEPTH

        nested: object = "leaf"
        for _ in range(MAX_DEPTH + 5):
            nested = [nested]
        bounded = _bound_value(nested)
        cursor = bounded
        depth = 0
        while isinstance(cursor, list):
            cursor = cursor[0]
            depth += 1
        assert depth <= MAX_DEPTH + 1
