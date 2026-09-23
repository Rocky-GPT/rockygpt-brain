"""Schools are Ramapo's current schools; former catalog names are reviewed legacy names."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from rockygpt_brain.retrieval.graph import GraphData
from rockygpt_brain.retrieval.profiles import ProfileQuery, ProfileSection
from test_profiles import NOW, repository

SNH = 'c5e91f2f-d073-4bfa-8f8b-819457982c99'
AHE = 'a0af6d36-ac17-4d2f-9a95-ccb68cd98772'
PROGRAM = '846f1609-bf42-4b3e-af21-857cdf581f07'
PERSON = '84984049-2698-44eb-a046-eb0f282ba527'
FACULTY_KEY = 'https://example.edu/faculty/ada#email=ada@example.edu'
CAPTURED = '2026-09-23T12:31:43Z'


def school(entity_id: str, name: str, section: str, *aliases: str) -> dict[str, Any]:
    return {'id': entity_id, 'kind': 'school', 'name': name, 'aliases': list(aliases),
            'links': [{'collection': 'schools', 'source_key': 'ramapo-schools',
                       'source_record_keys': [section]}]}


def placed(collection: str, source: str, key: str) -> dict[str, Any]:
    return {'type': 'part_of', 'target_entity_id': SNH, 'evidence': [
        {'collection': collection, 'source_key': source, 'source_record_key': key,
         'field': 'school'}]}


def school_data(program_school: str = 'School of Theoretical and Applied Science',
                profile_school: str = 'School of Science, Nursing, and Health (Adjunct)') -> Any:
    data = repository()
    for key in ('ramapo-schools', 'academic-programs', 'faculty'):
        data.sources[key] = {**data.sources['directory'], 'id': key, 'source_key': key,
                             'provenance_status': 'static', 'completed_at': NOW}
    data._artifacts['campus-identities']['entities'] += [
        school(SNH, 'School of Science, Nursing, and Health', 'snh', 'SNH',
               'School of Theoretical and Applied Science'),
        school(AHE, 'School of Arts, Humanities, and Education', 'ahe', 'AHE'),
        {'id': PROGRAM, 'kind': 'program', 'name': 'Computer Science BS', 'aliases': [],
         'links': [{'collection': 'programs', 'source_key': 'academic-programs',
                    'source_record_keys': ['TAS:Computer Science BS']}],
         'relationships': [placed('programs', 'academic-programs', 'TAS:Computer Science BS')]},
        {'id': PERSON, 'kind': 'person', 'name': 'Ada Example', 'aliases': [],
         'links': [{'collection': 'faculty', 'source_key': 'faculty',
                    'source_record_keys': [FACULTY_KEY]}],
         'relationships': [placed('faculty', 'faculty', FACULTY_KEY)]},
    ]
    data._artifacts['campus-schools'] = {
        'schema_version': 1, 'captured_at': CAPTURED, 'schools': [
        {'section': 'snh', 'name': 'School of Science, Nursing, and Health', 'abbreviation': 'SNH',
         'url': 'https://www.ramapo.edu/snh/', 'legacy_names': [
             {'name': 'School of Theoretical and Applied Science',
              'evidence': '/tas/ redirects to /snh/'}]},
        {'section': 'ahe', 'name': 'School of Arts, Humanities, and Education',
         'abbreviation': 'AHE', 'url': 'https://www.ramapo.edu/ahe/', 'legacy_names': []},
    ]}
    data._artifacts['faculty'] = [{'name': 'Ada Example', 'email': 'ada@example.edu',
                                   'profileUrl': 'https://example.edu/faculty/ada/',
                                   'school': profile_school}]
    data._artifacts['catalog-conveners'] = None
    data._artifacts['programs'] = {'schools': []}
    program_row = {'id': 'program', 'name': 'Computer Science BS', 'school': program_school,
                   'source_id': 'academic-programs', 'source_record_key': 'TAS:Computer Science BS',
                   'total': 1, 'collected_at': NOW, 'valid_from': None, 'valid_until': None}

    def fetch(_sql: Any, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        return [program_row] if program_row['source_record_key'] in params[2] else []

    data._fetch = fetch  # type: ignore[method-assign, assignment]
    return data


def profile(data: Any, entity: str, *include: ProfileSection, **query: Any) -> dict[str, Any]:
    output: dict[str, Any] = data.lookup_profile(
        ProfileQuery(entity_id=UUID(entity), include=list(include), **query))
    return output


def test_school_section_is_the_official_page_with_its_reviewed_former_names() -> None:
    output = profile(school_data(), SNH, 'school')
    assert output['components']['school']['evidence_ids'] == ['schools:snh']
    record = output['records'][0]
    assert (record['fields']['abbreviation'], record['url']) == (
        'SNH', 'https://www.ramapo.edu/snh/')
    legacy = record['fields']['legacy_names'][0]['name']
    assert legacy == 'School of Theoretical and Applied Science'
    assert (record['source_key'], record['freshness']) == ('ramapo-schools', 'static')
    assert record['collected_at'].startswith('2026-09-23T12:31:43')
    assert any('was split' in text for text in record['limitations'])


def test_programs_and_people_reach_the_current_school_in_both_directions() -> None:
    data = school_data()
    program = profile(data, PROGRAM, 'related', relationship='part_of')['components']['related']
    assert [(r['entity']['id'], r['evidence_ids']) for r in program['relationships']] == [
        (SNH, ['programs:program'])]
    person = profile(data, PERSON, 'related')['components']['related']
    assert [r['entity']['id'] for r in person['relationships']] == [SNH]
    incoming = profile(data, SNH, 'related', direction='incoming')['components']['related']
    assert {r['entity']['id'] for r in incoming['relationships']} == {PROGRAM, PERSON}
    assert any('not complete' in text for text in incoming['limitations'])


def test_a_placement_whose_school_field_no_longer_names_the_school_is_not_returned() -> None:
    for data, entity in (
        (school_data(profile_school='School of Science, Nursing, and Health (Retired)'), PERSON),
        (school_data(profile_school='School of Arts, Humanities, and Education'), PERSON),
        (school_data(program_school='School of Social Science and Human Services'), PROGRAM),
    ):
        component = profile(data, entity, 'related')['components']['related']
        assert (component['relationships'], component['unverified_relationships']) == ([], 1)


def test_a_school_placement_must_cite_a_published_school_field() -> None:
    data = school_data()
    person = data._artifacts['campus-identities']['entities'][-1]
    person['relationships'][0]['evidence'][0]['field'] = 'department'
    output = profile(data, PERSON, 'related')
    assert (output['status'], output['reason']) == ('unavailable', 'invalid_identity_registry')


def test_inspector_locates_a_school_in_its_published_artifact() -> None:
    record = GraphData(school_data(), None).record('schools', 'schools:ahe')
    assert (record['artifact_key'], record['artifact_path']) == ('campus-schools', ['schools', '1'])
