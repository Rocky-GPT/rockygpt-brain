"""Entity-first projection never converts similar names into factual edges."""
from __future__ import annotations

from typing import Any
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.retrieval.knowledge import KnowledgeGraph, course_id
from test_profiles import ENTITY_ID, repository


@pytest.fixture
def data() -> Any:
    value = repository()
    value._artifacts["campus-identity-coverage"] = {"unresolved": []}
    value._artifacts['courses'] = [{"code": "CMPS 101", "title": "Intro", "credits": 4}]
    course = {"id": "courses:0", "title": "CMPS 101 — Intro", "source_key": "catalog",
              "source_record_key": "CMPS 101", "fields": {"code": "CMPS 101"}}
    value._load_artifact_records = Mock(return_value=[course])  # type: ignore[method-assign]
    return value


def add_course_link(data: Any, record_id: str | None = None) -> None:
    reference = {"collection": "courses", "source_key": "catalog",
                 "source_record_key": "CMPS 101"}
    if record_id is not None:
        reference['source_record_id'] = record_id
    data._artifacts['campus-identities']['entities'][0]['relationships'] = [{
        "type": "profile_course", "target_record": reference,
        "evidence": [{"collection": "faculty", "source_key": "faculty",
                      "source_record_key": "published-profile", "field": "courses"}],
    }]


def test_courses_are_stable_source_scoped_entities_with_exact_relationships(data: Any) -> None:
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
def test_ambiguous_or_broken_course_references_do_not_create_edges(data: Any, failure: str) -> None:
    add_course_link(data, '0' if failure != 'stale_id' else '99')
    if failure == 'duplicate':
        data._load_artifact_records.return_value *= 2
    if failure == 'missing':
        data._load_artifact_records.return_value = []
    graph = KnowledgeGraph(data).index()
    assert graph['edges'] == []
    assert any(issue['reason'] == 'unresolved_relationship' for issue in graph['diagnostics'])


def test_matching_property_text_does_not_create_relationship(data: Any) -> None:
    data._load_artifact_records.return_value[0]['title'] = 'Example Center'
    graph = KnowledgeGraph(data).index()
    assert len(graph['nodes']) == 2
    assert graph['edges'] == []


def test_api_development_gate_and_release_pin(data: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    params = {'entity_id': ENTITY_ID, 'dataset_version': 'old'}
    monkeypatch.setenv('BRAIN_ENVIRONMENT', 'production')
    with patch('rockygpt_brain.api.identities.CampusData') as factory:
        assert TestClient(app).get('/v1/dev/graph/knowledge', params=params).status_code == 404
        factory.assert_not_called()
    monkeypatch.setenv('BRAIN_ENVIRONMENT', 'development')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    with patch('rockygpt_brain.api.identities.CampusData', return_value=data):
        assert TestClient(app).get('/v1/dev/graph/knowledge', params=params).status_code == 409
    data._load_artifact_records.assert_not_called()


def test_knowledge_is_release_pinned_without_model_calls(
    data: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('BRAIN_ENVIRONMENT', 'development')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused')
    params = {'dataset_version': 'test-release',
              'entity_id': course_id('catalog', 'CMPS 101')}
    with (patch('rockygpt_brain.api.identities.CampusData', return_value=data),
          patch('rockygpt_brain.api.app.open_gateway') as gateway):
        response = TestClient(app).get('/v1/dev/graph/knowledge', params=params)
    assert response.status_code == 200
    assert response.json()['dataset_version'] == 'test-release'
    assert response.json()['identity_hash']
    gateway.assert_not_called()
