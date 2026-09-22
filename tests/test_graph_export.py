"""The download exports the authoritative graph, not a UI neighborhood or page."""
from __future__ import annotations

import copy
import os
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, Mock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.api.graph import _snapshot
from rockygpt_brain.retrieval.data import CampusData
from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.graph_export import export_graph
from rockygpt_brain.retrieval.knowledge import KnowledgeGraph, course_id
from test_profiles import ENTITY_ID, IDENTITY, repository


@pytest.fixture
def data() -> Any:
    value: Any = repository()
    value.connection = MagicMock()
    value.sources['catalog'] = {**value.sources['directory'], 'id': 'catalog',
                               'source_key': 'academic-programs'}
    value._artifacts['courses'] = {
        f'COURSE {i}': {'name': f'Course {i}', 'credits': 4} for i in range(137)
    }
    value._artifacts['campus-identity-coverage'] = {
        'identity_count': 1, 'additional_published_metadata': {'original': True},
        'unresolved': [{'reason': 'missing_explicit_id', 'record': str(i),
                        'extra_source_detail': {'keep': i}} for i in range(1103)],
    }
    value._artifacts['campus-identities']['entities'][0]['relationships'] = [{
        'type': 'profile_course', 'target_record': {
            'collection': 'courses', 'source_key': 'academic-programs',
            'source_record_key': 'COURSE 136', 'source_record_id': 'COURSE 136'},
        'evidence': [{'collection': 'faculty', 'source_key': 'faculty',
                      'source_record_key': 'explicit-profile', 'source_record_id': '12',
                      'field': 'courses', 'source_url': 'https://example.edu/profile'}],
    }]
    sources = copy.deepcopy(list(value.sources.values()))

    def fetch(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        assert params == ('dataset-one',)
        if 'FROM rockygpt_v2.sources' in query:
            return copy.deepcopy(sources)
        if 'FROM rockygpt_v2.dataset_versions' in query:
            return [{**value.dataset, 'status': 'active', 'activated_at': value.now,
                     'quality_summary': {'issues': list(range(151))}}]
        if 'FROM rockygpt_v2.release_artifacts' in query:
            return [{'artifact_key': key, 'content_hash': f'original-{key}',
                     'created_at': value.now} for key in value._artifacts]
        raise AssertionError(query)

    value._fetch = Mock(side_effect=fetch)
    return value


def test_export_preserves_entire_graph_bindings_original_reports_and_evidence(data: Any) -> None:
    before = copy.deepcopy(data._artifacts)
    expected = KnowledgeGraph(data).index()
    with patch.object(GraphData, 'browse', side_effect=AssertionError('No paginated reads')):
        result = export_graph(data, _snapshot(data, None))
    assert result['counts']['nodes'] == 138
    assert result['counts']['nodes_by_kind'] == {'office': 1, 'course': 137}
    assert [{k: node[k] for k in ('id', 'kind', 'name', 'aliases')}
            for node in result['nodes']] == expected['nodes']
    assert [{k: edge[k] for k in ('source', 'target', 'type', 'evidence')}
            for edge in result['edges']] == expected['edges']
    assert result['nodes'][0]['source_bindings'] == IDENTITY['links']
    course = next(n for n in result['nodes'] if n['id'] == course_id(
        'academic-programs', 'COURSE 136'))
    assert course['source_bindings'][0]['source_record_ids'] == ['COURSE 136']
    assert course['provenance']['path'] == ['COURSE 136']
    assert result['identity_registry'] == before['campus-identities']
    assert result['coverage'] == before['campus-identity-coverage']
    assert result['counts']['published_coverage_issues'] == 1103
    assert result['snapshot']['dataset']['quality_summary']['issues'] == list(range(151))
    declaration = result['published_relationships'][0]
    published = before['campus-identities']['entities'][0]['relationships'][0]
    assert declaration['published'] == published
    assert declaration['provenance']['path'] == ['entities', 0, 'relationships', 0]
    assert result['edges'][0]['evidence'][0]['source_record_id'] == '12'
    assert result['completeness']['truncated'] is False
    assert data._artifacts == before


def test_link_arrays_stay_independent_and_relationship_repeats_survive(data: Any) -> None:
    owner = data._artifacts['campus-identities']['entities'][0]
    owner['links'][0]['source_record_keys'] = ['first', 'second']
    owner['links'][0]['source_record_ids'] = ['one', 'two', 'three']
    owner['relationships'] *= 2
    result = export_graph(data, _snapshot(data, None))
    assert result['nodes'][0]['source_bindings'] == owner['links']
    assert len(result['edges']) == 2
    assert len({edge['id'] for edge in result['edges']}) == 2
    assert all(edge['source'] == ENTITY_ID for edge in result['edges'])
    assert result['counts']['relationships_by_type'] == {'profile_course': 2}


@pytest.mark.parametrize('failure', ['missing_identity', 'missing_course', 'stale_course_pin'])
def test_unresolved_declarations_retain_targets_and_evidence(data: Any, failure: str) -> None:
    rel = data._artifacts['campus-identities']['entities'][0]['relationships'][0]
    if failure == 'missing_identity':
        rel['type'] = 'convener'
        rel['target_entity_id'] = str(uuid5(NAMESPACE_URL, 'missing'))
        del rel['target_record']
    elif failure == 'missing_course':
        rel['target_record']['source_record_key'] = 'Not published'
    else:
        rel['target_record']['source_record_id'] = 'stale'
    result = export_graph(data, _snapshot(data, None))
    assert result['edges'] == []
    assert result['counts']['published_relationships'] == 1
    assert result['counts']['unresolved_relationships'] == 1
    assert result['unresolved_relationships'][0]['published'] == rel
    assert result['unresolved_relationships'][0]['resolved_target'] is None
    assert result['published_relationships'][0]['resolved_target'] is None


def test_equal_names_do_not_create_links_or_reverse_edges(data: Any) -> None:
    owner = data._artifacts['campus-identities']['entities'][0]
    other = {**copy.deepcopy(IDENTITY), 'id': str(uuid5(NAMESPACE_URL, 'another')),
             'links': [{'collection': 'contacts', 'source_key': 'directory',
                        'source_record_keys': ['unique-source-reference']}]}
    owner['relationships'] = [{'type': 'convener', 'target_entity_id': other['id'],
                               'evidence': owner['relationships'][0]['evidence']}]
    data._artifacts['campus-identities']['entities'].append(other)
    result = export_graph(data, _snapshot(data, None))
    assert result['counts']['edges'] == 1
    assert result['edges'][0]['source'] == owner['id']
    assert result['edges'][0]['target'] == other['id']


@pytest.mark.parametrize('failure', ['missing', 'malformed', 'partial_reader'])
def test_unreadable_catalog_never_yields_partial_export(data: Any, failure: str) -> None:
    if failure == 'partial_reader':
        data._load_artifact_records = Mock(return_value=[])
    else:
        data._artifacts['courses'] = None if failure == 'missing' else []
    with pytest.raises(HTTPException) as error:
        export_graph(data, _snapshot(data, None))
    assert error.value.status_code == 503


def test_missing_coverage_is_explicit_not_zero_issues(data: Any) -> None:
    data._artifacts['campus-identity-coverage'] = None
    result = export_graph(data, _snapshot(data, None))
    assert result['coverage'] is None
    assert result['counts']['published_coverage_issues'] is None
    assert {'reason': 'identity_coverage_unavailable'} in result['diagnostics']


def test_release_switched_during_connection_bootstrap_requires_retry(data: Any) -> None:
    original_fetch = data._fetch.side_effect

    def changed_fetch(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = original_fetch(query, params)
        if 'FROM rockygpt_v2.dataset_versions' in query:
            result[0]['status'] = 'retired'
        return result

    data._fetch.side_effect = changed_fetch
    with pytest.raises(HTTPException) as error:
        export_graph(data, _snapshot(data, None))
    assert error.value.status_code == 409


def test_endpoint_uses_current_snapshot_without_ui_filters_or_models(
    data: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('BRAIN_ENVIRONMENT', 'development')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    connection = data.connection
    with (patch('rockygpt_brain.api.identities.CampusData', return_value=data),
          patch('rockygpt_brain.api.app.open_gateway') as gateway):
        response = TestClient(app).get('/v1/dev/graph/export', params={
            'limit': 1, 'offset': 100000, 'entity_id': ENTITY_ID, 'filters': '{}'})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert 'attachment;' in response.headers['content-disposition']
    assert response.json()['counts']['nodes'] == 138
    connection.execute.assert_called_once_with(
        'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    gateway.assert_not_called()


def test_endpoint_disabled_outside_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BRAIN_ENVIRONMENT', 'production')
    with patch('rockygpt_brain.api.identities.CampusData') as factory:
        assert TestClient(app).get('/v1/dev/graph/export').status_code == 404
    factory.assert_not_called()


@pytest.mark.skipif(not os.getenv('GRAPH_TEST_DATABASE_URL'), reason='Opt-in read-only database')
def test_live_export_equals_published_canonical_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ['GRAPH_TEST_DATABASE_URL']
    data = CampusData(url, datetime.now(UTC))
    try:
        data._ensure_loaded()
        expected = KnowledgeGraph(data).index()
    finally:
        data.close()
    monkeypatch.setenv('BRAIN_ENVIRONMENT', 'development')
    monkeypatch.setenv('DATABASE_URL', url)
    response = TestClient(app).get('/v1/dev/graph/export')
    assert response.status_code == 200, response.text[:500]
    result = response.json()
    assert [{k: node[k] for k in ('id', 'kind', 'name', 'aliases')}
            for node in result['nodes']] == expected['nodes']
    assert [{k: edge[k] for k in ('source', 'target', 'type', 'evidence')}
            for edge in result['edges']] == expected['edges']
    assert result['counts']['nodes_by_kind'] == dict(Counter(n['kind'] for n in expected['nodes']))
    assert len(result['published_relationships']) == sum(
        len(entity.get('relationships', [])) for entity in result['identity_registry']['entities'])
    assert result['counts']['published_coverage_issues'] == len(result['coverage']['unresolved'])
