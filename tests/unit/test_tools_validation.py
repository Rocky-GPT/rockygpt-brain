from datetime import UTC, datetime

from rockygpt_brain.brain.grounding import ProvenanceRegistry
from rockygpt_brain.brain.time_context import resolve_time_context
from rockygpt_brain.brain.tools import (
    MAX_RECORDS_PER_CALL,
    TOOL_HANDLERS,
    ToolPayload,
    execute_tool,
    summarize,
    validate_arguments,
)
from rockygpt_brain.brain.tools.bounding import _bound_value
from rockygpt_brain.data_client.models import Dataset, SearchResult

_SEARCH_CAMPUS_HOURS = TOOL_HANDLERS["search_campus_hours"]
_SEARCH_SHUTTLES = TOOL_HANDLERS["search_shuttles"]


class TestValidateArguments:
    def test_valid_arguments_pass(self) -> None:
        result = validate_arguments(_SEARCH_CAMPUS_HOURS, {"q": "library", "day": "Monday"})
        assert result == {"q": "library", "day": "Monday"}

    def test_empty_arguments_ok(self) -> None:
        assert validate_arguments(_SEARCH_CAMPUS_HOURS, {}) == {}

    def test_non_dict_rejected(self) -> None:
        assert validate_arguments(_SEARCH_CAMPUS_HOURS, None) is None
        assert validate_arguments(_SEARCH_CAMPUS_HOURS, ["q", "library"]) is None
        assert validate_arguments(_SEARCH_CAMPUS_HOURS, "library") is None

    def test_unknown_key_rejected(self) -> None:
        assert validate_arguments(_SEARCH_CAMPUS_HOURS, {"q": "library", "evil": "x"}) is None

    def test_wrong_type_value_rejected(self) -> None:
        assert validate_arguments(_SEARCH_CAMPUS_HOURS, {"q": 123}) is None

    def test_oversized_value_rejected(self) -> None:
        assert validate_arguments(_SEARCH_CAMPUS_HOURS, {"q": "x" * 201}) is None

    def test_enum_violation_rejected(self) -> None:
        assert validate_arguments(_SEARCH_CAMPUS_HOURS, {"day": "Someday"}) is None

    def test_valid_enum_value_passes(self) -> None:
        assert validate_arguments(_SEARCH_SHUTTLES, {"serviceDay": "weekday"}) == {
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
        # The model-visible part is unchanged: a small, fixed, non-reflective
        # error. `_defect` rides alongside for the operator log and is stripped
        # by the orchestrator before the result is shown to the model.
        assert result["error"] == "invalid_arguments"
        assert result["_defect"] == "unknown_key:other"
        assert set(result) == {"error", "_defect"}

    async def test_defect_names_a_plausible_invented_key(self) -> None:
        registry = ProvenanceRegistry()
        time_context = resolve_time_context(now=None, timezone_name=None)
        result = await execute_tool(
            "search_events",
            {"q": "today", "date": "2026-08-24"},
            client=None,
            time_context=time_context,
            registry=registry,
        )
        assert result["_defect"] == "unknown_key:date"

    async def test_defect_never_retains_a_model_invented_name(self) -> None:
        registry = ProvenanceRegistry()
        time_context = resolve_time_context(now=None, timezone_name=None)
        result = await execute_tool(
            "search_events",
            {"totally_made_up_field": "eve@example.com"},
            client=None,
            time_context=time_context,
            registry=registry,
        )
        # Neither the invented key nor its value appears anywhere.
        assert result["_defect"] == "unknown_key:other"
        assert "totally_made_up_field" not in str(result)
        assert "eve@example.com" not in str(result)


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
        envelope = summarize(ToolPayload.from_search(result), registry=registry)
        assert len(envelope["records"]) <= MAX_RECORDS_PER_CALL
        # The extra records past the cap must never become citable.
        assert registry.resolve([f"src-{MAX_RECORDS_PER_CALL + 2}"]) is None

    def test_shown_record_source_id_matches_registry(self) -> None:
        registry = ProvenanceRegistry()
        result = SearchResult(
            dataset=Dataset(id="d1", version="v1", activated_at=datetime(2024, 1, 1, tzinfo=UTC)),
            records=[_record("src-1")],
        )
        envelope = summarize(ToolPayload.from_search(result), registry=registry)
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
        envelope = summarize(ToolPayload.from_search(result), registry=registry)
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


class TestSearchMap:
    """`/v1/map` is the one campus endpoint returning no per-record source,
    so the brain mints one from the location itself. These pin the rules
    that make those minted citations trustworthy."""

    class _MapClient:
        def __init__(self, response: object) -> None:
            self._response = response
            self.queries: list[object] = []

        async def map(self, *, q: object = None) -> object:
            self.queries.append(q)
            return self._response

    _LOCATION = {
        "key": "building_arch-havemeyer",
        "name": "Arch (Havemeyer)",
        "type": "building",
        "mapUrl": "https://www.ramapo.edu/map/?building=Arch",
    }

    async def test_location_is_citable_under_a_namespaced_source_id(self) -> None:
        registry = ProvenanceRegistry()
        client = self._MapClient({"locations": [self._LOCATION]})
        envelope = await execute_tool(
            "search_map",
            {"q": "arch"},
            client=client,  # type: ignore[arg-type]
            time_context=resolve_time_context(now=None, timezone_name=None),
            registry=registry,
        )
        shown_id = envelope["records"][0]["sourceId"]
        assert shown_id == "map:building_arch-havemeyer"
        citations = registry.resolve([shown_id])
        assert citations is not None
        # Title and URL come from the data service's own record, never the model.
        assert citations[0].title == "Arch (Havemeyer)"
        assert citations[0].url == "https://www.ramapo.edu/map/?building=Arch"

    async def test_location_key_is_exposed_for_view_map_actions(self) -> None:
        # A VIEW_MAP uiAction needs a real locationKey; the model can only
        # use one it was shown.
        registry = ProvenanceRegistry()
        client = self._MapClient({"locations": [self._LOCATION]})
        envelope = await execute_tool(
            "search_map",
            {},
            client=client,  # type: ignore[arg-type]
            time_context=resolve_time_context(now=None, timezone_name=None),
            registry=registry,
        )
        assert envelope["records"][0]["key"] == "building_arch-havemeyer"

    async def test_location_without_a_usable_url_is_shown_but_not_citable(self) -> None:
        registry = ProvenanceRegistry()
        client = self._MapClient(
            {"locations": [{**self._LOCATION, "mapUrl": "javascript:alert(1)"}]}
        )
        envelope = await execute_tool(
            "search_map",
            {},
            client=client,  # type: ignore[arg-type]
            time_context=resolve_time_context(now=None, timezone_name=None),
            registry=registry,
        )
        assert "sourceId" not in envelope["records"][0]
        assert registry.known_source_ids() == []

    async def test_map_envelope_omits_dataset_fields(self) -> None:
        # /v1/map carries no dataset version, and claiming one would be a lie.
        registry = ProvenanceRegistry()
        client = self._MapClient({"locations": [self._LOCATION]})
        envelope = await execute_tool(
            "search_map",
            {},
            client=client,  # type: ignore[arg-type]
            time_context=resolve_time_context(now=None, timezone_name=None),
            registry=registry,
        )
        assert "datasetId" not in envelope
        assert "datasetVersion" not in envelope

    async def test_resolved_match_leads_the_records(self) -> None:
        # /v1/map returns every campus location unfiltered and reports the
        # query's match only in `resolved`. Without hoisting it, the
        # per-call cap would drop the one location the model asked for.
        registry = ProvenanceRegistry()
        target = {
            "key": "office_csi",
            "name": "Center for Student Involvement (CSI)",
            "type": "office",
            "mapUrl": "https://www.ramapo.edu/map/?office=CSI",
        }
        filler = [
            {
                "key": f"building_{i}",
                "name": f"Building {i}",
                "type": "building",
                "mapUrl": f"https://www.ramapo.edu/map/?building={i}",
            }
            for i in range(MAX_RECORDS_PER_CALL + 5)
        ]
        client = self._MapClient({"locations": [*filler, target], "resolved": target})
        envelope = await execute_tool(
            "search_map",
            {"q": "CSI office"},
            client=client,  # type: ignore[arg-type]
            time_context=resolve_time_context(now=None, timezone_name=None),
            registry=registry,
        )
        first = envelope["records"][0]
        assert first["key"] == "office_csi"
        assert first["bestMatch"] is True
        # Present exactly once, not duplicated by the hoist.
        assert [r["key"] for r in envelope["records"]].count("office_csi") == 1

    async def test_no_resolved_match_still_lists_locations(self) -> None:
        registry = ProvenanceRegistry()
        client = self._MapClient({"locations": [self._LOCATION], "resolved": None})
        envelope = await execute_tool(
            "search_map",
            {"q": "nothing matches this"},
            client=client,  # type: ignore[arg-type]
            time_context=resolve_time_context(now=None, timezone_name=None),
            registry=registry,
        )
        assert envelope["records"][0]["key"] == "building_arch-havemeyer"
        assert "bestMatch" not in envelope["records"][0]
