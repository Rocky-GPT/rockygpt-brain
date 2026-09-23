"""Room prefixes place people and offices in buildings; a building is only a location."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.profiles import ProfileQuery, ProfileSection
from test_profiles import ENTITY_ID, NOW, repository

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
