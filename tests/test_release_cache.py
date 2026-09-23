"""Release-derived structures are built once per published release, never across releases."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import Mock, patch

import pytest

from rockygpt_brain.retrieval import release_cache
from rockygpt_brain.retrieval.data import CampusData
from rockygpt_brain.retrieval.knowledge import KnowledgeGraph, release_graph
from rockygpt_brain.retrieval.release_cache import cached
from test_profiles import NOW, repository


@pytest.fixture(autouse=True)
def empty_cache() -> Iterator[None]:
    release_cache.clear()
    yield
    release_cache.clear()


class Release:
    def __init__(self, fingerprint: tuple[str, ...] | None) -> None:
        self.fingerprint = fingerprint

    def release_fingerprint(self) -> tuple[str, ...] | None:
        return self.fingerprint


def test_values_are_built_once_per_release_fingerprint() -> None:
    build = Mock(side_effect=object)
    first = cached(Release(("db", "release-1", "campus-identities=a")), "graph", build)
    assert cached(Release(("db", "release-1", "campus-identities=a")), "graph", build) is first
    assert build.call_count == 1
    # A changed artifact hash, another release or another structure is built anew.
    for fingerprint, name in [(("db", "release-1", "campus-identities=b"), "graph"),
                              (("db", "release-2", "campus-identities=a"), "graph"),
                              (("db", "release-1", "campus-identities=a"), "readiness")]:
        assert cached(Release(fingerprint), name, build) is not first
    assert build.call_count == 4


def test_without_a_live_database_nothing_is_cached() -> None:
    build = Mock(side_effect=object)
    assert cached(Release(None), "graph", build) is not cached(Release(None), "graph", build)
    assert build.call_count == 2


def test_only_the_most_recent_releases_are_kept() -> None:
    for number in range(release_cache.LIMIT + 1):
        cached(Release(("db", f"release-{number}")), "graph", object)
    build = Mock(side_effect=object)
    cached(Release(("db", f"release-{release_cache.LIMIT}")), "graph", build)
    assert build.call_count == 0
    cached(Release(("db", "release-0")), "graph", build)
    assert build.call_count == 1


def test_fingerprint_names_the_database_release_and_every_artifact_hash() -> None:
    data = CampusData("postgresql://reader@db.example:5432/campus", NOW)
    data.dataset = {"id": "dataset-one", "version": "release-1", "activated_at": "activated"}
    # A repository without a database connection has nothing to fingerprint.
    assert data.release_fingerprint() is None
    data.connection = Mock()
    data._fetch = Mock(return_value=[  # type: ignore[method-assign]
        {"artifact_key": "campus-identities", "content_hash": "abc"},
        {"artifact_key": "courses", "content_hash": "def"},
    ])
    assert data.release_fingerprint() == (
        "db.example", "5432", "campus", "dataset-one", "release-1", "activated",
        "campus-identities=abc", "courses=def")
    data.release_fingerprint()
    data._fetch.assert_called_once()


def request(fingerprint: tuple[str, ...]) -> Any:
    data = repository()
    data._artifacts["campus-identity-coverage"] = {"unresolved": []}
    data._load_artifact_records = Mock(return_value=[])  # type: ignore[method-assign]
    data.release_fingerprint = Mock(return_value=fingerprint)  # type: ignore[method-assign]
    return data


def test_requests_on_one_release_share_its_graph_and_identity_hash() -> None:
    first, second = request(("db", "release-1")), request(("db", "release-1"))
    with patch("rockygpt_brain.retrieval.knowledge.KnowledgeGraph",
               wraps=KnowledgeGraph) as built:
        graph = release_graph(first)
        assert release_graph(second) is graph
        assert built.call_count == 1
        assert release_graph(request(("db", "release-2"))) is not graph
    assert graph.index["nodes"][0]["name"] == "Example Center"
    with patch.object(CampusData, "_identity_readiness",
                      return_value={"status": "available", "artifact_hash": "h"}) as computed:
        assert first.identity_readiness() == second.identity_readiness()
        assert computed.call_count == 1
        # Callers receive a copy; the cached answer cannot be changed through one.
        first.identity_readiness()["status"] = "changed"
        assert second.identity_readiness()["status"] == "available"
