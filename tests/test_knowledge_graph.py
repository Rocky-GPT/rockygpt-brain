"""Entity-first projection never converts similar names into factual edges."""
from __future__ import annotations

import copy
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.knowledge import KnowledgeGraph, course_id
from test_profiles import ENTITY_ID, repository


@pytest.fixture
def data():
    value = repository()
    value._artifacts["campus-identity-coverage"] = {"unresolved": []}
    value._artifacts['courses'] = [{"code": "CMPS 101", "title": "Intro", "credits": 4}]
    course = {"id": "courses:0", "title": "CMPS 101 — Intro", "source_key": "catalog",
              "source_record_key": "CMPS 101", "fields": {"code": "CMPS 101"}}
    value._load_artifact_records = Mock(return_value=[course])
    return value


def add_course_link(data, record_id=None):
    reference = {"collection": "courses", "source_key": "catalog",
                 "source_record_key": "CMPS 101"}
    if record_id is not None:
        reference['source_record_id'] = record_id
    data._artifacts['campus-identities']['entities'][0]['relationships'] = [{
        "type": "profile_course", "target_record": reference,
        "evidence": [{"collection": "faculty", "source_key": "faculty",
                      "source_record_key": "published-profile", "field": "courses"}],
    }]


def test_courses_are_stable_source_scoped_entities_with_exact_relationships(data):
    add_course_link(data)
    graph = KnowledgeGraph(data).index()
    course = next(node for node in graph['nodes'] if node['kind'] == 'course')
    assert course['id'] == course_id('catalog', 'CMPS 101')
    assert course['id'] != course_id('another catalog', 'CMPS 101')
    assert graph['edges'][0]['target'] == course['id']
    assert graph['edges'][0]['source'] == ENTITY_ID
    assert graph['edges'][0]['type'] == 'profile_course'  # Never assert teaches.
    assert graph['edges'][0]['evidence'][0]['field'] == 'courses'
    data._load_artifact_records.return_value[0]['id'] = 'courses:99'
    assert KnowledgeGraph(data).index()['nodes'][-1]['id'] == course['id']


@pytest.mark.parametrize('failure', ['duplicate', 'stale_id', 'missing'])
def test_ambiguous_or_broken_course_references_do_not_create_edges(data, failure):
    add_course_link(data, '0' if failure != 'stale_id' else '99')
    if failure == 'duplicate':
        data._load_artifact_records.return_value *= 2
    if failure == 'missing':
        data._load_artifact_records.return_value = []
    graph = KnowledgeGraph(data).index()
    assert graph['edges'] == []
    assert any(issue['reason'] == 'unresolved_relationship' for issue in graph['diagnostics'])


def test_matching_property_text_does_not_create_relationship(data):
    data._load_artifact_records.return_value[0]['title'] = 'Example Center'
    graph = KnowledgeGraph(data).index()
    assert len(graph['nodes']) == 2
    assert graph['edges'] == []


def test_properties_keep_sources_conflicts_and_pagination(data):
    records = [{"id": f"contacts:{index}", "fields": {"phone": phone, "zero": 0,
                "empty": "", "unknown": None, "enabled": False}, "collected_at": "yesterday",
                "source_key": f"source-{index}", "raw_record": {"internal": True}}
               for index, phone in enumerate(['x123', 'x456'])]
    before = copy.deepcopy(records)
    with (patch.object(GraphData, 'browse', return_value={
        'records': records, 'total': 7, 'next_offset': 2,
    }) as browse, patch.object(GraphData, 'record', side_effect=records)):
        output = KnowledgeGraph(data).properties(UUID(ENTITY_ID), 'contacts', 0, 2)
    assert output['groups'][0]['total'] == 7
    assert output['groups'][0]['next_offset'] == 2
    assert [row['fields']['phone'] for row in output['groups'][0]['records']] == ['x123', 'x456']
    assert output['groups'][0]['records'][0]['fields']['enabled'] is False
    assert 'raw_record' not in output['groups'][0]['records'][0]
    assert records == before
    browse.assert_called_once_with('contacts', {}, None, 0, 2)


def test_course_properties_reuse_exact_catalog_record(data):
    node_id = course_id('catalog', 'CMPS 101')
    result = KnowledgeGraph(data).properties(UUID(node_id), None, 0, 8)
    assert result['groups'][0]['records'][0]['fields']['credits'] == 4
    assert result['entity_id'] == node_id


def test_properties_cannot_read_unlinked_collections(data):
    with pytest.raises(HTTPException) as error:
        KnowledgeGraph(data).properties(UUID(ENTITY_ID), 'events', 0, 8)
    assert error.value.status_code == 422


@pytest.mark.parametrize('operation', ['knowledge', 'properties'])
def test_api_development_gate_and_release_pin(data, monkeypatch, operation):
    params = {'entity_id': ENTITY_ID, 'dataset_version': 'old'}
    monkeypatch.setenv('BRAIN_ENVIRONMENT', 'production')
    with patch('rockygpt_brain.api.identities.CampusData') as factory:
        assert TestClient(app).get(f'/v1/dev/graph/{operation}', params=params).status_code == 404
        factory.assert_not_called()
    monkeypatch.setenv('BRAIN_ENVIRONMENT', 'development')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    with patch('rockygpt_brain.api.identities.CampusData', return_value=data):
        assert TestClient(app).get(f'/v1/dev/graph/{operation}', params=params).status_code == 409
    data._load_artifact_records.assert_not_called()


def test_missing_property_does_not_hide_other_published_values(data):
    record = {"id": "contacts:good", "fields": {"phone": "x123"}}
    with (patch.object(GraphData, 'browse', return_value={
        'records': [{'id': 'contacts:missing'}, record], 'total': 2, 'next_offset': None,
    }), patch.object(GraphData, 'record', side_effect=[HTTPException(404, 'Missing'), record])):
        output = KnowledgeGraph(data).properties(UUID(ENTITY_ID), 'contacts', 0, 8)
    assert output['groups'][0]['records'] == [record]
    assert output['diagnostics'][0]['reason'] == 'linked_property_unavailable'


def test_course_property_pagination_does_not_repeat_the_only_record(data):
    output = KnowledgeGraph(data).properties(UUID(course_id('catalog', 'CMPS 101')),
                                            'courses', 1, 8)
    assert output['groups'][0]['records'] == []
    assert output['groups'][0]['total'] == 1


@pytest.mark.parametrize('operation', ['knowledge', 'properties'])
def test_new_endpoints_return_release_pinned_data_without_model_calls(data, monkeypatch, operation):
    monkeypatch.setenv('BRAIN_ENVIRONMENT', 'development')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    params = {'dataset_version': 'test-release',
              'entity_id': course_id('catalog', 'CMPS 101')}
    with (patch('rockygpt_brain.api.identities.CampusData', return_value=data),
          patch('rockygpt_brain.api.app.open_gateway') as gateway):
        response = TestClient(app).get(f'/v1/dev/graph/{operation}', params=params)
    assert response.status_code == 200
    assert response.json()['dataset_version'] == 'test-release'
    assert response.json()['identity_hash']
    gateway.assert_not_called()
