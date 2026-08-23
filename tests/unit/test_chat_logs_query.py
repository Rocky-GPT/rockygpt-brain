"""list_logs builds one fixed SQL string (no request-derived text is ever
concatenated into it — see persistence/chat_logs.py) and passes optional
filters as bound parameters, with an *absent* filter expressed as a bound
`NULL` rather than by omitting a WHERE fragment. These tests use a fake
pool that just records what `fetch`/`fetchrow` were called with, so they
verify the exact bound arguments without needing a live database.
"""

from __future__ import annotations

from typing import Any

from rockygpt_brain.persistence.chat_logs import list_logs


class _FakeRecord(dict[str, Any]):
    """dict already supports __getitem__; this just documents intent."""


class _FakePool:
    def __init__(self) -> None:
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.fetch_calls.append((query, args))
        return []

    async def fetchrow(self, query: str, *args: Any) -> _FakeRecord:
        if "avg_latency_ms" in query:
            return _FakeRecord(
                total_logs=0,
                avg_latency_ms=0,
                unique_sessions=0,
                unique_visitors=0,
                error_count=0,
                client_count=0,
                dev_count=0,
                bot_count=0,
            )
        return _FakeRecord(max_updated=None, total=0)


async def test_empty_route_and_origin_lists_mean_no_filter() -> None:
    # An empty list is a caller saying "no constraint on this filter," not
    # "match nothing" — route = ANY('{}') would otherwise match zero rows.
    pool = _FakePool()
    await list_logs(pool, search=None, routes=[], origins=[], limit=50)

    assert len(pool.fetch_calls) == 1
    _query, args = pool.fetch_calls[0]
    search_pattern, routes_param, origins_param, limit_param = args
    assert search_pattern is None
    assert routes_param is None
    assert origins_param is None
    assert limit_param == 50


async def test_none_route_and_origin_also_mean_no_filter() -> None:
    pool = _FakePool()
    await list_logs(pool, search=None, routes=None, origins=None, limit=25)

    _query, args = pool.fetch_calls[0]
    _search_pattern, routes_param, origins_param, _limit_param = args
    assert routes_param is None
    assert origins_param is None


async def test_non_empty_filters_are_passed_through_as_bound_parameters() -> None:
    pool = _FakePool()
    await list_logs(
        pool, search="library hours", routes=["standard"], origins=["client"], limit=10
    )

    _query, args = pool.fetch_calls[0]
    search_pattern, routes_param, origins_param, limit_param = args
    assert search_pattern == "%library hours%"
    assert routes_param == ["standard"]
    assert origins_param == ["client"]
    assert limit_param == 10


async def test_query_text_is_fixed_regardless_of_filters() -> None:
    # The whole point of the rewrite: the SQL *text* never varies with the
    # filters supplied, only the bound values do.
    pool = _FakePool()
    await list_logs(pool, search=None, routes=None, origins=None, limit=1)
    await list_logs(pool, search="x", routes=["a", "b"], origins=["dev"], limit=2)

    first_query, _ = pool.fetch_calls[0]
    second_query, _ = pool.fetch_calls[1]
    assert first_query == second_query
