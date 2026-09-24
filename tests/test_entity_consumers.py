"""Brain retrieval and evidence review share canonical entity property semantics."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID

import test_projection
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.governance.evidence import bounded_result
from rockygpt_brain.retrieval.entity_evidence import attach_entity_navigation
from rockygpt_brain.retrieval.exact import ContactQuery, contact_answer
from rockygpt_brain.retrieval.knowledge import course_id
from rockygpt_brain.retrieval.models import EntityQuery, SearchQuery
from rockygpt_brain.retrieval.profiles import ProfileQuery
from test_profile_sections import PERSON, person_data
from test_profiles import NOW
from test_projection import ENTITY_ID, Fixture

fixture = test_projection.fixture


def properties(output: dict[str, Any]) -> dict[str, Any]:
    return {p['key']: p for p in output['entity_facts']['properties']}


def test_contact_uses_entity_sources_and_one_email_without_directory_only_bypass() -> None:
    data = person_data()
    output = data.lookup_contact(ContactQuery(entity='Professor Example',
                                              fields=['email', 'phone']))
    facts = properties(output)
    assert output['resolution']['entity']['id'] == PERSON
    assert output['match'] == 'canonical_entity'
    assert set(facts) == {'name', 'email', 'phones'}
    assert facts['email']['status'] == 'known'
    assert [v['value'] for v in facts['email']['values']] == ['ada@example.edu']
    assert facts['email']['values'][0]['supporting_evidence_ids'] == ['contacts:ada', 'faculty:0']
    assert facts['phones']['status'] == 'conflicting'
    assert output['field_status'] == {'email': 'known', 'phone': 'conflicting'}
    assert data._fetch.call_count == 1
    assert data._fetch.call_args.args[1] == ('dataset-one', 'directory', ['person:ada'])
    assert {r['id'] for r in output['records']} == {'contacts:ada', 'faculty:0'}
    assert contact_answer([ChatMessage(role='user', content="What is Professor Example's email?")],
                          ContactQuery(entity='Professor Example', fields=['email']),
                          output, NOW.date()) is None


def test_profile_canonical_aliases_preserve_raw_values_and_unknown_preference() -> None:
    data = person_data()
    row = data._fetch.return_value[0]
    row.update(phone='(201) 555-9999', phones=[{'number': '201-555-9999'}],
               offices=['A-101'], prefers_email=False)
    before = deepcopy(row)
    output = data.lookup_profile(ProfileQuery(entity='Ada Example', include=['contact', 'faculty']))
    facts = properties(output)
    assert facts['phones']['status'] == 'known'
    assert len(facts['phones']['values']) == 1
    assert facts['phones']['values'][0]['evidence_count'] == 2
    assert facts['offices']['status'] == 'known'
    assert facts['prefers_email']['status'] == 'unknown'
    assert facts['prefers_email']['assertions'][0]['value'] is False
    assert row == before
    assert len(facts['email']['assertions']) == 2  # Sections do not duplicate evidence.


def test_missing_source_keeps_available_value_but_marks_incomplete() -> None:
    data = person_data()
    data._artifacts['faculty'] = []
    output = data.lookup_contact(ContactQuery(entity='Ada Example', fields=['email']))
    assert properties(output)['email']['status'] == 'known'
    assert output['entity_facts']['properties_complete'] is False
    assert output['entity_facts']['coverage'][0]['reason'] == 'profile_component_incomplete'


def test_ambiguous_identity_never_reads_one_contact_or_joins_by_email() -> None:
    data = person_data()
    other = deepcopy(data._artifacts['campus-identities']['entities'][0])
    other['id'] = '67b4f5ae-bc47-41a7-9a7b-012dc5a2f598'
    other['links'] = [{'collection': 'contacts', 'source_key': 'directory',
                       'source_record_keys': ['person:other']}]
    data._artifacts['campus-identities']['entities'].append(other)
    output = data.lookup_contact(ContactQuery(entity='Ada Example', fields=['email']))
    assert output['resolution']['status'] == 'ambiguous'
    assert output['records'] == [] and 'entity_facts' not in output
    data._fetch.assert_not_called()


def test_search_returns_exact_entity_navigation_and_courses_are_followable() -> None:
    data = person_data()
    profile = data.lookup_profile(ProfileQuery(entity='Ada Example', include=['contact']))
    data._cache['contacts'] = [data._seen['contacts:ada']]
    output = data.search(SearchQuery(collection='contacts', query='Ada'))
    assert output['records'][0]['canonical_entity_id'] == PERSON
    assert output['entity_navigation']['entities'][0]['id'] == PERSON
    assert output['entity_navigation']['next'] == 'lookup_entity'
    courses = data._load('courses')
    nav = attach_entity_navigation(data, courses)
    assert courses[0]['canonical_entity_id'] == course_id('academic-programs', 'COMP 101')
    assert nav['entities'][0]['kind'] == 'course'
    assert properties(profile)['email']['status'] == 'known'


def test_entity_lookup_hydrates_exact_citations_without_reread(fixture: Fixture) -> None:
    data, snapshot, _ = fixture
    data.identity_readiness = Mock(return_value={'artifact_hash': snapshot['identity_hash']})  # type: ignore[method-assign]
    data._fetch = Mock(side_effect=AssertionError('No second source read is allowed'))  # type: ignore[method-assign]
    output = data.lookup_entity(EntityQuery(entity_id=UUID(ENTITY_ID), properties=['email']))
    assert properties(output)['email']['status'] == 'conflicting'
    assert len(output['records']) == 2
    assert all(r['canonical_entity_id'] == ENTITY_ID for r in output['records'])
    assert all(set(r['fields']) == {'email'} for r in output['records'])
    assert set(data._seen) == {'contacts:1', 'contacts:2'}
    data._fetch.assert_not_called()


def test_course_entity_lookup_uses_same_read_model_and_original_artifact_citation() -> None:
    data = person_data()
    data._artifacts['campus-identity-coverage'] = {'unresolved': []}
    data.identity_readiness = Mock(return_value={'artifact_hash': 'test-identities'})
    entity_id = course_id('academic-programs', 'COMP 101')
    with patch.object(data, '_fetch', side_effect=AssertionError('Artifact course needs no SQL')):
        output = data.lookup_entity(EntityQuery(entity_id=UUID(entity_id), properties=['name']))
    fact = properties(output)['name']
    assert fact['status'] == 'known'
    assert fact['values'][0]['supporting_evidence_ids'] == ['courses:COMP 101']
    assert output['records'][0]['fields'] == {'name': 'Introduction to Computing'}
    assert any('enrollment' in note for note in output['records'][0]['limitations'])


def test_delivery_budget_cannot_leave_facts_without_their_citations() -> None:
    data = person_data()
    output = data.lookup_profile(ProfileQuery(entity='Ada Example', include=['contact']))
    limited = bounded_result(output, lambda result: len(result.get('records', [])) <= 1)
    assert len(limited['records']) == 1
    assert 'entity_facts' not in limited
    assert limited['entity_facts_withheld'] == 'retrieval_delivery_limit'


def test_entity_tool_reaches_review_with_shared_property_coverage() -> None:
    import json
    from types import SimpleNamespace

    from rockygpt_brain.core.engine import run_turn
    from test_engine import answer, review, tools

    data = person_data()
    data._artifacts['campus-identity-coverage'] = {'unresolved': []}
    data.identity_readiness = Mock(return_value={'artifact_hash': 'test-identities'})
    entity_id = course_id('academic-programs', 'COMP 101')
    client = Mock()
    client.create.side_effect = [
        tools(SimpleNamespace(type='function_call', name='lookup_entity', call_id='entity',
                              arguments=json.dumps({'entity_id': entity_id,
                                                    'properties': ['name']}))),
        answer('COMP 101 is Introduction to Computing.', 'campus_fact', ['courses:COMP 101']),
        review('supported'),
    ]
    result = run_turn(
        [ChatMessage(role='user', content='What is the name of COMP 101?')],
        client=client, data=data, model='test', now=NOW,
    )
    assert result['metrics']['reviewCalls'] == 1
    trace = result['trace'][0]
    assert trace['tool'] == 'lookup_entity'
    assert trace['entity_facts']['properties'] == [
        {'key': 'name', 'status': 'known', 'evidence_ids': ['courses:COMP 101']}]
    reviewer = json.loads(client.create.call_args_list[-1].kwargs['input'])
    assert reviewer['retrieval_coverage'][0]['entity_facts']['properties'][0]['status'] == 'known'


def test_contact_lineage_uses_hydrated_metadata_without_returning_unrequested_school() -> None:
    data = person_data()
    data._artifacts['faculty'][0]['school'] = 'Example School'
    row = data._fetch.return_value[0]
    row.update(source_id='faculty', source_record_key='faculty:ada-example:example-school')
    link = data._artifacts['campus-identities']['entities'][0]['links'][0]
    link.update(source_key='faculty', source_record_keys=[row['source_record_key']])
    output = data.lookup_contact(ContactQuery(entity='Ada Example', fields=['email']))
    sources = {source['id']: source for source in output['entity_facts']['sources']}
    assert sources['contacts:ada']['derived_from_source_id'] == 'faculty:0'
    assert all('school' not in record['fields'] for record in output['records'])
    assert data._fetch.call_count == 1


def test_legacy_coverage_does_not_call_missing_preference_a_published_false() -> None:
    data = person_data()
    data._fetch.return_value[0]['prefers_email'] = False
    output = data.lookup_profile(ProfileQuery(entity='Ada Example', include=['contact']))
    assert properties(output)['prefers_email']['status'] == 'unknown'
    assert output['components']['contact']['fields']['prefers_email'] == 'not_published'
    record = next(r for r in output['records'] if r['collection'] == 'contacts')
    assert record['fields']['prefers_email'] is False
    assert record['coverage']['fields']['prefers_email'] == 'not_published'
    assert data._seen[record['id']]['coverage']['fields']['prefers_email'] == 'not_published'


def test_entity_event_date_never_turns_storage_midnight_into_a_published_clock(
    fixture: Fixture,
) -> None:
    from rockygpt_brain.retrieval.graph import GraphData

    data, snapshot, records = fixture
    entity = data._artifacts['campus-identities']['entities'][0]
    entity.update(kind='event', name='Date-only event', links=[{
        'collection': 'events', 'source_key': 'directory', 'source_record_keys': ['k']}])
    raw = {
        'id': 'date-only', 'source_id': 'directory', 'source_record_key': 'k',
        'title': 'Date-only event', 'starts_at': '2026-09-21T00:00:00-04:00',
        'date_label': 'September 21', 'start_time': None, 'end_time': None,
        'collected_at': NOW.isoformat(), 'valid_from': None, 'valid_until': None,
    }
    records['events'] = [GraphData(data)._record(
        'events', {'record': raw, 'source': data.sources['directory']})]
    data.identity_readiness = Mock(return_value={'artifact_hash': snapshot['identity_hash']})  # type: ignore[method-assign]
    output = data.lookup_entity(EntityQuery(entity_id=UUID(ENTITY_ID), properties=['starts_at']))
    assert properties(output)['starts_at']['values'][0]['value'] == '2026-09-21'
    assert output['records'][0]['coverage']['fields']['starts_at'] == 'date_only'
    assert any('start time is unavailable' in note for note in output['records'][0]['limitations'])
