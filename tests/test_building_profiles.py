"""Room prefixes place people and offices in buildings; a building is only a location."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import test_projection
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.governance.evidence import bounded_result
from rockygpt_brain.retrieval.exact import ContactQuery
from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.models import EntityQuery
from rockygpt_brain.retrieval.profiles import ProfileQuery, ProfileSection
from test_engine import answer, review, tools
from test_profiles import ENTITY_ID, NOW, repository
from test_projection import Fixture

BUILDING_D = '5b0f7e0c-1f7d-5b52-9a53-3a1f8f4b0c11'
BUILDING_ASB = '5b0f7e0c-1f7d-5b52-9a53-3a1f8f4b0c12'
PERSON = '84984049-2698-44eb-a046-eb0f282ba527'
MAP_TIME = '2026-08-27T16:58:11.587Z'


def building(entity_id: str, name: str, location: str) -> dict[str, Any]:
    return {'id': entity_id, 'kind': 'building', 'name': name, 'aliases': [],
            'links': [{'collection': 'buildings', 'source_key': 'campus-map',
                       'source_record_keys': [location]}]}


def room(kind: str, target: str, source: str, key: str) -> dict[str, Any]:
    return {'type': kind, 'target_entity_id': target, 'evidence': [
        {'collection': 'contacts', 'source_key': source, 'source_record_key': key,
         'field': 'office'}]}


def building_data(office_room: str = 'D-224') -> Any:
    data = repository()
    data.sources['campus-map'] = {
        **data.sources['directory'], 'id': 'campus-map', 'source_key': 'campus-map',
        'title': 'Ramapo Campus Map', 'canonical_url': 'https://map.ramapo.edu/',
        'provenance_status': 'static', 'completed_at': NOW,
    }
    data.sources['faculty'] = {
        **data.sources['directory'], 'id': 'faculty', 'source_key': 'faculty'}
    entities = data._artifacts['campus-identities']['entities']
    entities[0]['relationships'] = [room('located_at', BUILDING_D, 'directory', 'office:ec')]
    entities += [
        building(BUILDING_D, 'Academic Building D', '1133371'),
        building(BUILDING_ASB, 'Anisfield School of Business (ASB)', '1133424'),
        {'id': PERSON, 'kind': 'person', 'name': 'Ada Example', 'aliases': [],
         'links': [{'collection': 'contacts', 'source_key': 'faculty',
                    'source_record_keys': ['faculty:ada']}],
         'relationships': [room('office_at', BUILDING_ASB, 'faculty', 'faculty:ada')]},
    ]
    data._artifacts['campus-buildings'] = {
        'schema_version': 1, 'map_generated_at': MAP_TIME, 'buildings': [
        {'concept3d_id': '1133371', 'name': 'Academic Building D', 'category': 'Academic Buildings',
         'room_prefixes': ['D'], 'map_url': 'https://map.ramapo.edu/?id=2292#!m/1133371?sbc/'},
        {'concept3d_id': '1133424', 'name': 'Anisfield School of Business (ASB)',
         'category': 'Academic Buildings', 'room_prefixes': ['ASB'],
         'map_url': 'https://map.ramapo.edu/?id=2292#!m/1133424?sbc/'},
    ]}
    common = {'collected_at': NOW, 'total': 1, 'valid_from': None, 'valid_until': None}
    contacts = {
        'office:ec': {**common, 'id': 'contact', 'source_id': 'directory',
                      'source_record_key': 'office:ec', 'name': 'Example Center',
                      'office': office_room},
        'faculty:ada': {**common, 'id': 'ada', 'source_id': 'faculty',
                        'source_record_key': 'faculty:ada', 'name': 'Ada Example',
                        'office': 'ASB-409'},
    }

    def fetch(_sql: Any, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        return [contacts[key] for key in params[2] if key in contacts]

    data._fetch = fetch  # type: ignore[method-assign, assignment]
    return data


def profile(data: Any, entity: str, *include: ProfileSection, **query: Any) -> dict[str, Any]:
    output: dict[str, Any] = data.lookup_profile(
        ProfileQuery(entity_id=UUID(entity), include=list(include), **query))
    return output


def test_building_section_is_the_map_record_with_its_own_collection_time() -> None:
    output = profile(building_data(), BUILDING_D, 'building')
    component = output['components']['building']
    assert (component['status'], component['evidence_ids']) == ('available', ['buildings:1133371'])
    record = output['records'][0]
    assert record['fields']['room_prefixes'] == ['D']
    assert record['url'] == 'https://map.ramapo.edu/?id=2292#!m/1133371?sbc/'
    assert (record['source_key'], record['freshness']) == ('campus-map', 'static')
    assert record['collected_at'].startswith('2026-08-27T16:58:11')
    assert any('not a complete building directory' in text for text in record['limitations'])


def test_rooms_are_followed_both_ways_with_their_contact_evidence() -> None:
    data = building_data()
    outgoing = profile(data, ENTITY_ID, 'related', relationship='located_at')
    [relationship] = outgoing['components']['related']['relationships']
    assert (relationship['type'], relationship['entity']['id']) == ('located_at', BUILDING_D)
    assert relationship['evidence_ids'] == ['contacts:contact']
    assert outgoing['records'][0]['fields']['office'] == 'D-224'
    incoming = profile(data, BUILDING_D, 'related', direction='incoming')
    assert [(r['type'], r['entity']['id']) for r in
            incoming['components']['related']['relationships']] == [('located_at', ENTITY_ID)]
    assert any('not a complete building directory' in text
               for text in incoming['components']['related']['limitations'])
    person = profile(data, PERSON, 'related')['components']['related']
    assert [(r['type'], r['entity']['id']) for r in person['relationships']] == [
        ('office_at', BUILDING_ASB)]
    assert any('not the person\'s school' in text for text in person['limitations'])


def test_a_room_whose_prefix_no_longer_names_the_building_is_not_returned() -> None:
    for moved in ('E-100', 'Learning Commons 204A', 'D-224 / Lobby'):
        component = profile(building_data(moved), ENTITY_ID, 'related')['components']['related']
        assert (component['relationships'], component['unverified_relationships']) == ([], 1), moved
    # Two published rooms in different buildings both still place the office.
    component = profile(building_data('D-224 / ASB-100'), ENTITY_ID,
                        'related')['components']['related']
    assert [r['entity']['id'] for r in component['relationships']] == [BUILDING_D]


def test_a_room_relationship_must_cite_a_published_office() -> None:
    data = building_data()
    data._artifacts['campus-identities']['entities'][0]['relationships'][0]['evidence'][0][
        'field'] = 'department'
    output = profile(data, ENTITY_ID, 'related')
    assert (output['status'], output['reason']) == ('unavailable', 'invalid_identity_registry')


def test_inspector_locates_a_building_in_its_published_artifact() -> None:
    record = GraphData(building_data(), None).record('buildings', 'buildings:1133424')
    assert record['artifact_key'] == 'campus-buildings'
    assert record['artifact_path'] == ['buildings', '1']
    assert record['raw_record']['name'] == 'Anisfield School of Business (ASB)'


def reviewed(data: Any, entity_id: str = ENTITY_ID) -> Any:
    """The office has no room; the building's own record carries its reviewed placement."""
    source = 'https://www.ramapo.edu/about/campus-hours/'
    data._artifacts['campus-identities']['entities'][0]['relationships'] = [{
        'type': 'located_at', 'target_entity_id': BUILDING_D, 'evidence': [
            {'collection': 'buildings', 'source_key': 'campus-map', 'source_record_key': '1133371',
             'field': 'reviewed_locations', 'source_url': source}]}]
    data._artifacts['campus-buildings']['buildings'][0]['reviewed_locations'] = [
        {'entity_id': entity_id, 'entity': 'Example Center', 'reviewed_at': '2026-09-24',
         'statement': 'Example Center in Academic Building D', 'source_url': source}]
    return data


def test_a_reviewed_statement_places_an_office_that_has_no_room() -> None:
    data = reviewed(building_data(''))
    outgoing = profile(data, ENTITY_ID, 'related')['components']['related']
    [relationship] = outgoing['relationships']
    assert (relationship['type'], relationship['entity']['id']) == ('located_at', BUILDING_D)
    assert relationship['evidence_ids'] == ['buildings:1133371']
    assert 'reviewed official statement' in relationship['meaning']
    assert [text for text in outgoing['limitations'] if 'room' in text] == [
        'Placed by an official page\'s statement that a person reviewed, not by a room number; '
        'it gives no room.']
    incoming = profile(data, BUILDING_D, 'related', direction='incoming')
    [relationship] = incoming['components']['related']['relationships']
    assert (relationship['entity']['id'], relationship['meaning']) == (
        ENTITY_ID, 'This building\'s campus map record carries a reviewed official statement '
                   'that places the related office here.')
    assert incoming['records'][0]['fields']['reviewed_locations'][0]['statement'] == (
        'Example Center in Academic Building D')


def test_a_reviewed_statement_must_name_the_office_on_that_buildings_record() -> None:
    other = reviewed(building_data(''), PERSON)
    missing = reviewed(building_data(''))
    del missing._artifacts['campus-buildings']['buildings'][0]['reviewed_locations']
    for data in (other, missing):
        component = profile(data, ENTITY_ID, 'related')['components']['related']
        assert (component['relationships'], component['unverified_relationships']) == ([], 1)
    # A building statement is never evidence for a person's office.
    data = building_data()
    data._artifacts['campus-identities']['entities'][-1]['relationships'][0]['evidence'][0].update(
        collection='buildings', source_key='campus-map', source_record_key='1133424',
        field='reviewed_locations')
    output = profile(data, PERSON, 'related')
    assert (output['status'], output['reason']) == ('unavailable', 'invalid_identity_registry')


def test_a_reviewed_reading_places_only_that_exact_published_office_text() -> None:
    def read(room: str, readings: list[str]) -> Any:
        data = building_data(room)
        data._artifacts['campus-buildings']['buildings'][0]['reviewed_rooms'] = readings
        return profile(data, ENTITY_ID, 'related')['components']['related']

    component = read('Learning Commons 204A', ['Learning Commons 204A'])
    [relationship] = component['relationships']
    assert (relationship['entity']['id'], relationship['evidence_ids']) == (
        BUILDING_D, ['contacts:contact'])
    assert relationship['meaning'] == ('A person reviewed this entry\'s published office text as '
                                       'naming the related building.')
    assert any('not a room number' in text for text in component['limitations'])
    for room, readings in (('Learning Commons 204', ['Learning Commons 204A']),
                           ('Learning Commons 204A', [])):
        component = read(room, readings)
        assert (component['relationships'], component['unverified_relationships']) == ([], 1)


def test_contact_names_the_building_its_published_room_places_it_in() -> None:
    output = profile(building_data(), ENTITY_ID, 'contact')
    [placement] = output['components']['contact']['placement']
    assert (placement['type'], placement['entity']['id']) == ('located_at', BUILDING_D)
    assert (placement['evidence_ids'], placement['target_evidence_ids']) == (
        ['contacts:contact'], ['buildings:1133371'])
    contact, building = output['records']
    assert (contact['fields']['office'], contact['canonical_entity_id']) == ('D-224', ENTITY_ID)
    assert building['fields']['room_prefixes'] == ['D'] and 'canonical_entity_id' not in building
    assert any('room number\'s prefix' in text for text in building['limitations'])
    # The building is where the office is, not a source of the office's own facts.
    assert {assertion['source_id'] for prop in output['entity_facts']['properties']
            for assertion in prop['assertions']} == {'contacts:contact'}
    person = profile(building_data(), PERSON, 'contact')['components']['contact']['placement']
    assert [(item['type'], item['entity']['id']) for item in person] == [
        ('office_at', BUILDING_ASB)]
    moved = profile(building_data('E-100'), ENTITY_ID, 'contact')['components']['contact']
    assert (moved['placement'], moved['status']) == ([], 'available')


def test_contact_places_an_office_with_no_room_by_its_reviewed_statement() -> None:
    data = reviewed(building_data(''))
    output = data.lookup_contact(ContactQuery(entity='Example Center', fields=['office']))
    [placement] = output['components']['contact']['placement']
    assert placement['evidence_ids'] == placement['target_evidence_ids'] == ['buildings:1133371']
    assert 'reviewed official statement' in placement['meaning']
    building = next(record for record in output['records'] if record['collection'] == 'buildings')
    assert building['fields']['reviewed_locations'][0]['statement'] == (
        'Example Center in Academic Building D')
    assert any('it gives no room' in text for text in building['limitations'])


def test_a_contact_conflict_is_not_marked_on_the_building() -> None:
    data = building_data()
    row = {'collected_at': NOW, 'total': 2, 'valid_from': None, 'valid_until': None,
           'source_id': 'directory', 'source_record_key': 'office:ec', 'name': 'Example Center',
           'office': 'D-224'}
    rows = [{**row, 'id': 'contact', 'phone': '201-555-0100'},
            {**row, 'id': 'other', 'phone': '201-555-0199'}]
    data._fetch = lambda _sql, params: rows if params[1] == 'directory' else []
    output = profile(data, ENTITY_ID, 'contact')
    assert output['components']['contact']['fields']['phone'] == 'conflict'
    building = next(record for record in output['records'] if record['collection'] == 'buildings')
    assert not any('disagree' in text for text in building['limitations'])


def test_the_reviewer_sees_where_a_contact_is() -> None:
    client = Mock()
    client.create.side_effect = [
        tools(SimpleNamespace(type='function_call', name='lookup_contact', call_id='contact',
                              arguments=json.dumps({'entity': 'Ramapo Example Center',
                                                    'fields': ['office'], 'request_text': None}))),
        answer('Example Center is in Academic Building D.', 'campus_fact', ['buildings:1133371']),
        review('supported'),
    ]
    result = run_turn([ChatMessage(role='user', content='Where is Example Center?')],
                      client=client, data=reviewed(building_data('')), model='test', now=NOW)
    assert result['status'] == 'answered'
    # The reviewer's retrieval coverage is this trace.
    [placement] = result['trace'][0]['components']['contact']['placement']
    assert (placement['entity']['name'], placement['evidence_ids']) == (
        'Academic Building D', ['buildings:1133371'])


fixture = test_projection.fixture


def placed_venue(fixture: Fixture, evidence: dict[str, Any], **building_fields: Any) -> Any:
    """The projection fixture's venue, placed in Academic Building D."""
    data: Any = fixture[0]
    snapshot = fixture[1]
    data.identity_readiness = Mock(return_value={'artifact_hash': snapshot['identity_hash']})
    data.sources['campus-map'] = {
        **data.sources['directory'], 'id': 'campus-map', 'source_key': 'campus-map',
        'provenance_status': 'static', 'completed_at': NOW}
    entities = data._artifacts['campus-identities']['entities']
    entities[0]['relationships'] = [
        {'type': 'located_at', 'target_entity_id': BUILDING_D, 'evidence': [evidence]}]
    entities.append(building(BUILDING_D, 'Academic Building D', '1133371'))
    data._artifacts['campus-buildings'] = {
        'schema_version': 1, 'map_generated_at': MAP_TIME, 'buildings': [
            {'concept3d_id': '1133371', 'name': 'Academic Building D',
             'category': 'Academic Buildings', 'room_prefixes': ['D'],
             'map_url': 'https://map.ramapo.edu/?id=2292#!m/1133371?sbc/', **building_fields}]}
    return data


def test_entity_lookup_names_the_building_a_reviewed_statement_places_it_in(
        fixture: Fixture) -> None:
    source = 'https://www.ramapo.edu/about/campus-hours/'
    data = placed_venue(fixture, {
        'collection': 'buildings', 'source_key': 'campus-map', 'source_record_key': '1133371',
        'field': 'reviewed_locations', 'source_url': source,
    }, reviewed_locations=[{'entity_id': ENTITY_ID, 'entity': 'Test venue',
                            'statement': 'Test venue in Academic Building D',
                            'source_url': source, 'reviewed_at': '2026-09-24'}])
    # The building's map record is an artifact: no second source read.
    data._fetch = Mock(side_effect=AssertionError('No SQL read is allowed'))
    output = data.lookup_entity(EntityQuery(entity_id=UUID(ENTITY_ID), properties=['email']))
    [placement] = output['placement']
    assert (placement['type'], placement['entity']['name']) == (
        'located_at', 'Academic Building D')
    assert placement['evidence_ids'] == placement['target_evidence_ids'] == ['buildings:1133371']
    building_record = next(r for r in output['records'] if r['id'] == 'buildings:1133371')
    assert building_record['fields']['reviewed_locations'][0]['statement'] == (
        'Test venue in Academic Building D')
    assert any('it gives no room' in text for text in building_record['limitations'])
    assert 'buildings:1133371' in data._seen
    # The venue's own facts come only from its own records.
    assert {a['source_id'] for p in output['entity_facts']['properties']
            for a in p['assertions']} == {'contacts:1', 'contacts:2'}
    # The reviewer's retrieval coverage is the turn's trace, which carries the placement.
    client = Mock()
    client.create.side_effect = [
        tools(SimpleNamespace(type='function_call', name='lookup_entity', call_id='entity',
                              arguments=json.dumps({'entity_id': ENTITY_ID,
                                                    'properties': None}))),
        answer('Test venue is in Academic Building D.', 'campus_fact', ['buildings:1133371']),
        review('supported'),
    ]
    result = run_turn([ChatMessage(role='user', content='Where is the test venue?')],
                      client=client, data=data, model='test', now=NOW)
    assert result['status'] == 'answered'
    assert [item['entity']['name'] for item in result['trace'][0]['placement']] == [
        'Academic Building D']


def test_entity_lookup_adds_only_the_room_that_places_it(fixture: Fixture) -> None:
    data = placed_venue(fixture, {'collection': 'contacts', 'source_key': 'directory',
                                  'source_record_key': 'k', 'field': 'office'})
    for place in ('raw_record', 'fields'):
        fixture[2]['contacts'][0][place]['office'] = 'D-224'
    row = {'id': '1', 'source_id': 'directory', 'source_record_key': 'k', 'total': 1,
           'collected_at': NOW, 'valid_from': None, 'valid_until': None,
           'name': 'Test venue', 'email': 'venue@example.edu', 'phone': 'one', 'office': 'D-224'}
    data._fetch = Mock(return_value=[row])

    def lookup(*properties: str) -> dict[str, Any]:
        output: dict[str, Any] = data.lookup_entity(
            EntityQuery(entity_id=UUID(ENTITY_ID), properties=list(properties)))
        return output

    output = lookup('email')
    [placement] = output['placement']
    assert (placement['evidence_ids'], placement['target_evidence_ids']) == (
        ['contacts:1'], ['buildings:1133371'])
    # The contact record stays the venue's own, with only the room added: an unrequested
    # phone, which the venue's records disagree on, is not delivered as settled.
    contact = next(r for r in output['records'] if r['id'] == 'contacts:1')
    assert contact['canonical_entity_id'] == ENTITY_ID
    assert contact['fields'] == {'email': 'venue@example.edu', 'office': 'D-224'}
    assert contact['coverage']['fields']['office'] == 'published'
    # Where the venue's records disagree on the room, the added room says so.
    for place in ('raw_record', 'fields'):
        fixture[2]['contacts'][1][place]['office'] = 'E-100'
    contact = next(r for r in lookup('email')['records'] if r['id'] == 'contacts:1')
    assert contact['coverage']['fields']['office'] == 'conflict'
    assert any('disagree on offices' in text for text in contact['limitations'])
    for place in ('raw_record', 'fields'):
        fixture[2]['contacts'][1][place]['office'] = None
    assert [r['id'] for r in output['records']] == [
        'contacts:1', 'contacts:2', 'buildings:1133371']
    assert output['total_matches'] == 2
    # A record already shared this turn is replaced, never edited in place.
    shared = data._seen['contacts:1']
    before = deepcopy(shared)
    lookup('email')
    assert shared == before
    # A placing record the lookup did not return comes with its room alone.
    output = lookup('website_url')
    contact = next(r for r in output['records'] if r['id'] == 'contacts:1')
    assert (contact['fields'], contact['canonical_entity_id']) == ({'office': 'D-224'}, ENTITY_ID)
    # Out of room, the placement goes first, and the venue's records return to what they
    # were: at exactly the size before the placement, the facts still arrive whole.
    output = lookup('email')
    before = {key: value for key, value in output.items()
              if key not in {'placement', '_unplaced_records'}}
    before['records'] = [output['_unplaced_records'].get(r['id'], r) for r in output['records']
                         if r['id'] != 'buildings:1133371']
    size = len(json.dumps(before))
    assert bounded_result(output, lambda value: len(json.dumps(value)) <= size) == before
    assert before['records'][0]['fields'] == {'email': 'venue@example.edu'}
    delivered = bounded_result(lookup('email'),
                               lambda value: len(json.dumps(value)) <= size + 100)
    assert (delivered['placement_withheld'], 'entity_facts' in delivered) == (
        'retrieval_delivery_limit', True)
    # The copies kept for that are never delivered.
    assert '_unplaced_records' not in bounded_result(lookup('email'), lambda value: True)
    # A placement that fails to load is reported, not shown as no building.
    data._fetch = Mock(side_effect=RuntimeError('database unavailable'))
    output = lookup('email')
    assert ('placement' in output, output['placement_withheld']) == (
        False, 'placement_unavailable')
