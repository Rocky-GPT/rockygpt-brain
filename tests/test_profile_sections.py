"""Cross-dataset identities preserve source scope, dates, and explicit relationships."""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from rockygpt_brain.api.app import app
from rockygpt_brain.campus.schedules import opening_intervals
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.retrieval.models import SearchQuery
from rockygpt_brain.retrieval.profiles import ProfileQuery
from test_engine import answer, review, tools
from test_profiles import ENTITY_ID, NOW, repository, rows

PERSON = '84984049-2698-44eb-a046-eb0f282ba527'
PROGRAM = '846f1609-bf42-4b3e-af21-857cdf581f07'
FACULTY_KEY = 'https://example.edu/faculty/ada#email=ada@example.edu'


def link(collection: str, source: str, *keys: str) -> dict[str, Any]:
    return {'collection': collection, 'source_key': source, 'source_record_keys': list(keys)}


def person_data() -> Any:
    data = repository()
    for source in ('faculty', 'academic-programs'):
        data.sources[source] = {
            **data.sources['directory'], 'id': source, 'source_key': source,
            'title': source, 'canonical_url': f'https://example.edu/{source}',
            'provenance_status': 'static', 'completed_at': None,
        }
    person = {
        'id': PERSON, 'kind': 'person', 'name': 'Ada Example', 'aliases': ['Professor Example'],
        'links': [link('contacts', 'directory', 'person:ada'),
                  link('faculty', 'faculty', FACULTY_KEY)],
    }
    data._artifacts['campus-identities']['entities'] = [person]
    data._artifacts['faculty'] = [{
        'name': 'Ada Example', 'profileUrl': 'https://example.edu/faculty/ada/',
        'email': 'ada@example.edu', 'phone': '201-555-9999', 'office': 'A-101',
        'courses': ['COMP 101: Introduction to Computing', 'Unmapped Seminar'],
    }]
    data._artifacts['courses'] = {
        'COMP 101': {'code': 'COMP 101', 'name': 'Introduction to Computing', 'credits': '4'},
    }
    data._artifacts['catalog-conveners'] = None
    contact, _ = rows()
    contact.update(id='ada', name='Ada Example', source_record_key='person:ada',
                   email='ada@example.edu', office='A-101')
    data._fetch = Mock(return_value=[contact])  # type: ignore[method-assign]
    return data


def test_contact_faculty_conflicts_do_not_erase_agreed_fields_or_invent_freshness() -> None:
    data = person_data()
    result = data.lookup_profile(ProfileQuery(entity='Professor Example', include=['contact']))
    assert len(result['records']) == 2
    contact = result['components']['contact']
    assert contact['fields']['phone'] == 'conflict'
    assert contact['fields']['email'] == 'published'
    faculty = next(r for r in result['records'] if r['collection'] == 'faculty')
    assert faculty['collected_at'] is None
    assert faculty['freshness'] == 'static'
    assert 'courses' not in faculty['fields']
    assert 'phone' in contact['conflicts']


def test_profile_courses_stay_undated_and_catalog_record_is_related_not_identical() -> None:
    data = person_data()
    data._artifacts['campus-identities']['entities'][0]['relationships'] = [{
        'type': 'profile_course',
        'target_record': {'collection': 'courses', 'source_key': 'academic-programs',
                          'source_record_key': 'COMP 101'},
        'evidence': [{'collection': 'faculty', 'source_key': 'faculty',
                      'source_record_key': FACULTY_KEY, 'field': 'courses'}],
    }]
    result = data.lookup_profile(ProfileQuery(entity_id=UUID(PERSON), include=['courses']))
    assert result['components']['courses']['temporal_scope'] == 'undated_profile_list'
    assert {r['collection'] for r in result['records']} == {'faculty', 'courses'}
    faculty, catalog = result['records']
    assert faculty['fields']['courses'][-1] == 'Unmapped Seminar'
    assert 'canonical_entity_id' not in catalog
    assert catalog['related_to_entity_id'] == PERSON
    assert any('not a current teaching assignment' in s for s in catalog['limitations'])
    assert 'office' not in faculty['fields']


def test_artifact_array_reorder_and_source_rename_keep_identity_and_original_evidence_ids() -> None:
    data = person_data()
    before = data.lookup_profile(ProfileQuery(entity_id=UUID(PERSON), include=['faculty']))
    original = data._artifacts['faculty'][0]
    original['name'] = 'Ada Renamed'
    data._artifacts['faculty'].insert(0, {
        'name': 'Other Person', 'email': 'other@example.edu',
        'profileUrl': 'https://example.edu/faculty/ada/',
    })
    data._cache.clear()
    after = data.lookup_profile(ProfileQuery(entity_id=UUID(PERSON), include=['faculty']))
    assert before['records'][0]['id'] == 'faculty:0'
    assert after['records'][0]['id'] == 'faculty:1'
    assert after['records'][0]['fields']['name'] == 'Ada Renamed'
    assert after['records'][0]['canonical_entity_id'] == PERSON
    assert after['records'][0]['source_record_key'] == FACULTY_KEY


def program_data() -> Any:
    data = person_data()
    program = {
        'id': PROGRAM, 'kind': 'program', 'name': 'Computer Science', 'aliases': [],
        'links': [link('programs', 'academic-programs', 'School:Computer Science')],
        'relationships': [{
            'type': 'convener', 'target_entity_id': PERSON,
            'evidence': [{'collection': 'programs', 'source_key': 'academic-programs',
                          'source_record_key': 'School:Computer Science',
                          'field': 'customFields.rJQmj',
                          'source_url': 'https://example.edu/computing'}],
        }],
    }
    data._artifacts['campus-identities']['entities'].append(program)
    data._artifacts['programs'] = {'schools': [{'school': 'School', 'majors': [{
        'name': 'Computer Science', 'convener': {'name': 'Ada Example',
                                               'email': 'ada@example.edu'},
        'catalogCode': 'TS-BS-COMP',
    }]}]}
    data._artifacts['catalog-conveners'] = {
        'collected_at': '2026-09-20T12:00:00Z',
        'source_url': 'https://example.edu/raw-catalog',
        'programs': [{
            'catalogCode': 'TS-BS-COMP', 'catalogUrl': 'https://example.edu/catalog/COMP',
            'customFields': {'rJQmj': '<p>Ada Example</p>'},
        }],
    }
    contact = data._fetch.return_value[0]
    program_row = {
        'id': 'program', 'name': 'Computer Science', 'source_id': 'academic-programs',
        'source_record_key': 'School:Computer Science', 'total': 1, 'collected_at': NOW,
        'valid_from': None, 'valid_until': None,
    }
    data._fetch = Mock(side_effect=lambda _sql, p:
                       [program_row] if p[1] == 'academic-programs' else [contact])
    return data


def test_program_convener_then_person_email_uses_normal_tool_calling_and_review() -> None:
    data = program_data()
    client = Mock()
    calls = []
    for index, arguments in enumerate([
        {'entity': 'Computer Science', 'include': ['conveners']},
        {'entity_id': PERSON, 'include': ['contact']},
    ]):
        calls.append(tools(SimpleNamespace(
            type='function_call', name='lookup_profile', call_id=f'profile{index}',
            arguments=json.dumps(arguments),
        )))
    client.create.side_effect = [*calls, answer(
        'Ada Example is the convener; their email is ada@example.edu.',
        'campus_fact', ['programs:program:convener', 'contacts:ada'],
    ), review('supported')]
    result = run_turn([
        ChatMessage(role='user', content="Who convenes Computer Science and what is their email?"),
    ], client=client, data=data, model='test', now=NOW)
    assert result['status'] == 'answered'
    assert result['metrics']['reviewCalls'] == 1
    first, second = result['trace']
    assert first['components']['conveners']['relationships'][0]['target']['id'] == PERSON
    assert second['resolution']['entity']['id'] == PERSON
    review_payload = json.loads(client.create.call_args_list[-1].kwargs['input'])
    assert review_payload['retrieval_coverage'][0]['components']['conveners']['relationships']


def test_raw_catalog_convener_preserves_its_timestamp_and_omits_inferred_legacy_field() -> None:
    data = program_data()
    school = data._artifacts['programs']['schools'][0]
    school['school'] = 'School'
    school['majors'][0].update(catalogCode='TS-BS-COMP', convener={'name': 'Wrong fallback'})
    data._artifacts['catalog-conveners'] = {
        'collected_at': '2026-08-30T12:00:00Z',
        'source_url': 'https://example.edu/raw-catalog',
        'programs': [{
            'catalogCode': 'TS-BS-COMP', 'catalogUrl': 'https://example.edu/catalog/COMP',
            'customFields': {'rJQmj': '<p>Ada Example</p>'},
        }],
    }
    relation = data._artifacts['campus-identities']['entities'][1]['relationships'][0]
    relation['evidence'][0]['field'] = 'customFields.rJQmj'
    output = data.lookup_profile(ProfileQuery(entity='Computer Science', include=['conveners']))
    original, explicit = output['records']
    assert 'convener' not in original['fields']
    assert original['collected_at'] == NOW.isoformat()
    assert explicit['collected_at'] == '2026-08-30T12:00:00+00:00'
    assert explicit['url'] == 'https://example.edu/catalog/COMP'
    assert output['components']['conveners']['relationships'][0]['evidence_ids'] == [
        'programs:program:convener',
    ]


@pytest.mark.parametrize('broken', ['target', 'evidence', 'field'])
def test_broken_convener_link_keeps_program_but_never_invents_person(broken: str) -> None:
    data = program_data()
    relation = data._artifacts['campus-identities']['entities'][1]['relationships'][0]
    if broken == 'target':
        relation['target_entity_id'] = ENTITY_ID
    elif broken == 'evidence':
        relation['evidence'][0]['source_record_key'] = 'missing'
    else:
        data._artifacts['catalog-conveners']['programs'][0]['customFields'].pop('rJQmj')
    result = data.lookup_profile(ProfileQuery(entity='Computer Science', include=['conveners']))
    assert len(result['records']) == (1 if broken == 'field' else 2)
    assert result['components']['conveners']['status'] == 'missing'
    assert result['components']['conveners']['relationships'] == []
    assert result['components']['conveners']['relationships_missing'] == 1


def test_broad_program_search_never_exposes_legacy_inferred_convener() -> None:
    data = program_data()
    data._artifacts['programs']['schools'][0]['majors'][0]['convener'] = {'name': 'Wrong fallback'}
    data._artifacts['catalog-conveners'] = None
    program_row = data._fetch(None, ('dataset', 'academic-programs'))[0]
    data._fetch = Mock(return_value=[program_row])
    output = data.search(SearchQuery(collection='programs', query='Computer Science'))
    assert len(output['records']) == 1
    assert 'convener' not in output['records'][0]['fields']
    data._cache.clear()
    data._artifacts['catalog-conveners'] = program_data()._artifacts['catalog-conveners']
    output = data.search(SearchQuery(collection='programs', query='Computer Science'))
    assert len(output['records']) == 2
    assert any('customFields' in row['fields'] for row in output['records'])


def dining_data() -> Any:
    data = repository()
    data.sources['dining'] = {**data.sources['hours'], 'id': 'dining', 'source_key': 'dining'}
    data._artifacts['campus-identities']['entities'][0].update(
        kind='venue', name='Example Dining', aliases=[],
        links=[link('dining_hours', 'dining', 'Monday', 'exception'),
               link('menu', 'dining', 'today:lunch', 'today:dinner', 'tomorrow:lunch')],
    )
    data._artifacts['menu-context'] = {'content': '# Example Dining Menu'}
    data._artifacts['dining-hours'] = []
    common = dict(collected_at=NOW, source_id='dining', total=2,
                  valid_from=None, valid_until=None)
    schedules = [{**common, 'id': 'regular', 'source_record_key': 'Monday',
                  'name': 'Example Dining', 'day': 'Monday',
                  'schedule': '11:00 AM - 02:00 PM; 05:00 PM - 12:00 AM'},
                 {**common, 'id': 'special', 'source_record_key': 'exception',
                  'name': 'Example Dining', 'day': 'Monday',
                  'valid_from': '2026-09-21', 'valid_until': '2026-09-21',
                  'schedule': '11:30 AM - 01:00 PM; 05:00 PM - 12:00 AM'}]
    menus = [{**common, 'total': 3, 'id': key, 'source_record_key': key,
              'name': name, 'meal': meal, 'valid_from': day, 'valid_until': day,
              'vegan': False, 'allergens': [], 'label_coverage': {}}
             for key, meal, name, day in [
                 ('today:lunch', 'Lunch', 'Soup', '2026-09-21'),
                 ('today:dinner', 'Dinner', 'Pasta', '2026-09-21'),
                 ('tomorrow:lunch', 'Lunch', 'Salad', '2026-09-22'),
             ]]
    data._fetch = Mock(side_effect=lambda _sql, params:
                       schedules if params[2][0] == 'Monday' else menus)  # type: ignore[method-assign]
    return data


def test_dining_date_meal_exceptions_split_hours_and_unknown_labels() -> None:
    data = dining_data()
    output = data.lookup_profile(ProfileQuery(
        entity='Example Dining', include=['hours', 'menu'], date=date(2026, 9, 21), meal='Lunch',
    ))
    assert len(output['records']) == 2
    hours, menu = output['records']
    assert hours['id'] == 'dining_hours:special:2026-09-21'
    assert hours['fields']['schedule'] == '11:30 AM - 01:00 PM; 05:00 PM - 12:00 AM'
    assert hours['fields']['meal_coverage'] == 'not_published'
    assert hours['fields']['requested_meal_periods'] == []
    assert menu['fields']['name'] == 'Soup'
    assert menu['relationship_to_entity'] == 'offering_at'
    assert 'canonical_entity_id' not in menu
    assert 'vegan' not in menu['fields']
    assert menu['coverage']['fields']['allergens'] == 'unknown'


def test_dining_explicit_meal_intervals_keep_split_service() -> None:
    data = dining_data()
    original_enrich = data._enrich

    def enrich(collection: str, records: list[dict[str, Any]]) -> None:
        original_enrich(collection, records)
        if collection == 'dining_hours':
            for record in records:
                record['fields']['periods'] = [
                    {'label': 'Lunch', 'start': '11:30 AM', 'end': '12:00 PM'},
                    {'label': 'Lunch', 'start': '12:15 PM', 'end': '01:00 PM'},
                    {'label': 'Dinner', 'start': '05:00 PM', 'end': '12:00 AM'},
                ]
    data._enrich = enrich
    output = data.lookup_profile(ProfileQuery(
        entity='Example Dining', include=['hours'], date=date(2026, 9, 21), meal='lunch',
    ))
    fields = output['records'][0]['fields']
    assert len(fields['requested_meal_periods']) == 2
    assert fields['meal_coverage'] == 'published'
    assert len(fields['periods']) == 3


def test_raw_dining_artifact_corrects_false_closed_and_preserves_unknown_exception() -> None:
    data = dining_data()
    schedules = data._fetch(None, ('dataset', 'dining', ['Monday']))
    schedules[1]['schedule'] = 'Closed (seasonal closure)'
    data._artifacts['dining-hours'] = [{
        'name': 'Example Dining',
        'openingHours': {
            'standardHours': [{
                'days': [{'value': 'Monday'}],
                'hours': [
                    {'label': 'Lunch', 'startTime': {'hour': '11', 'minute': '00', 'period': 'AM'},
                     'finishTime': {'hour': '02', 'minute': '00', 'period': 'PM'}},
                ],
            }],
            'seasonalHours': [{
                'from': '2026-09-21T04:00:00Z', 'to': '2026-09-22T03:59:00Z',
                'openingHours': [{'days': [{'value': 'Monday'}],
                                  'hours': [{'allDay': False}]}],
            }],
        },
    }]
    output = data.lookup_profile(ProfileQuery(
        entity='Example Dining', include=['hours'], date=date(2026, 9, 21), meal='Lunch',
    ))
    assert len(output['records']) == 1
    record = output['records'][0]
    assert record['id'] == 'dining_hours:special:2026-09-21'
    assert record['fields']['schedule'] == 'Hours unavailable'
    assert record['fields']['schedule_status'] == 'unknown_or_partial'
    assert record['fields']['requested_meal_periods'] == []
    assert record['collected_at'] == NOW.isoformat()
    with pytest.raises(ValueError):
        opening_intervals(record['fields']['schedule'], date(2026, 9, 21))


def test_labeled_split_dining_intervals_and_midnight_keep_campus_service_date() -> None:
    intervals = opening_intervals(
        'Lunch: 11:00 AM - 12:00 PM; Lunch: 12:15 PM - 02:00 PM; Dinner: 05:00 PM - 12:00 AM',
        date(2026, 9, 21),
    )
    assert len(intervals) == 3
    assert intervals[0][0].isoformat() == '2026-09-21T11:00:00-04:00'
    assert intervals[-1][1].isoformat() == '2026-09-22T00:00:00-04:00'
    assert opening_intervals('Closed (seasonal closure)', date(2026, 9, 21)) == []


@pytest.mark.parametrize('instant,expected', [
    (datetime(2026, 9, 22, 3, 59, tzinfo=UTC), '2026-09-21'),
    (datetime(2026, 9, 22, 4, 0, tzinfo=UTC), '2026-09-22'),
    (datetime(2026, 11, 1, 4, 30, tzinfo=UTC), '2026-11-01'),
    (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), '2026-11-01'),
])
def test_default_profile_date_is_campus_local_including_dst(
    instant: datetime, expected: str,
) -> None:
    data = dining_data()
    data.now = instant
    from rockygpt_brain.retrieval.models import CAMPUS_ZONE
    data.today = instant.astimezone(CAMPUS_ZONE).date()
    output = data.lookup_profile(ProfileQuery(entity='Example Dining', include=['hours', 'menu']))
    assert output['components']['hours']['service_date'] == expected
    assert output['components']['menu']['service_date'] == expected
    assert all(r['valid_from'] == expected for r in output['records'] if r['collection'] == 'menu')


def test_identity_readiness_is_stable_and_missing_map_is_optional() -> None:
    data = person_data()
    first = data.identity_readiness()
    data._artifacts['campus-identities'] = copy.deepcopy(data._artifacts['campus-identities'])
    assert data.identity_readiness() == first
    assert first['entity_count'] == 1 and len(first['artifact_hash']) == 64
    data._artifacts['campus-identities'] = None
    assert data.identity_readiness() == {'status': 'missing'}


@pytest.mark.parametrize('environment', ['development', 'production'])
def test_readiness_exposes_versions_only_in_development(environment: str) -> None:
    with patch('rockygpt_brain.api.app.load_deployment') as deploy, \
         patch('rockygpt_brain.api.app.PostgresLedger'), \
         patch('rockygpt_brain.api.app.CampusData') as data, \
         patch.dict('os.environ', {'DATABASE_URL': 'postgresql://unused'}):
        deploy.return_value.environment = environment
        data.return_value.readiness.return_value = {'dataset_version': 'v-test'}
        data.return_value.identity_readiness.return_value = {'status': 'missing'}
        output = TestClient(app).get('/readiness').json()
    assert output['status'] == 'ready'
    assert ('development' in output) == (environment == 'development')
