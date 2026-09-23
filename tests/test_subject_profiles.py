"""A course subject is the code in front of its courses; course search reads its names."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from rockygpt_brain.retrieval.models import SearchQuery
from rockygpt_brain.retrieval.profiles import IdentityRelationship, ProfileQuery
from rockygpt_brain.retrieval.subjects import course_subject, resolve_subjects
from test_profiles import NOW, repository

SUBJECT = '0c1a6e2b-4f55-5d0e-9a51-2c7b1f0e8d31'
PROGRAM = '846f1609-bf42-4b3e-af21-857cdf581f07'
CAPTURED = '2026-08-27T14:22:13.103Z'
SUBJECTS: dict[str, Any] = {'schema_version': 1, 'captured_at': CAPTURED, 'subjects': [
    {'code': 'CMPS', 'name': 'Computer Science', 'display_name': 'Computer Science (CMPS)',
     'search_terms': ['CS', 'Comp Sci'], 'course_count': 2},
    {'code': 'ARHT', 'name': 'Art History', 'display_name': 'Art History (ARHT)',
     'search_terms': [], 'course_count': 1},
    {'code': 'ARTS', 'name': 'Art', 'display_name': 'Art (ARTS)', 'search_terms': [],
     'course_count': 1},
    {'code': 'AIID', 'name': 'Interdisciplinary Studies',
     'display_name': 'Interdisciplinary Studies (AIID)', 'search_terms': [], 'course_count': 0},
    {'code': 'INTD', 'name': 'Interdisciplinary Studies',
     'display_name': 'Interdisciplinary Studies (INTD)', 'search_terms': [], 'course_count': 0},
    {'code': 'READ', 'name': None, 'display_name': 'READ', 'search_terms': [], 'course_count': 1},
]}


def includes(key: str) -> dict[str, Any]:
    reference = {'collection': 'courses', 'source_key': 'academic-programs',
                 'source_record_key': key}
    return {'type': 'includes_course', 'target_record': reference,
            'evidence': [{**reference, 'field': 'code'}]}


def subject_data(renumbered: bool = False) -> Any:
    data = repository()
    for key in ('course-subjects', 'academic-programs'):
        data.sources[key] = {**data.sources['directory'], 'id': key, 'source_key': key,
                             'provenance_status': 'static', 'completed_at': NOW}
    data._artifacts['campus-identities']['entities'] += [
        {'id': SUBJECT, 'kind': 'subject', 'name': 'Computer Science (CMPS)', 'aliases': ['CMPS'],
         'links': [{'collection': 'subjects', 'source_key': 'course-subjects',
                    'source_record_keys': ['CMPS']}],
         'relationships': [includes('CMPS 147'), includes('CMPS 148')]},
        {'id': PROGRAM, 'kind': 'program', 'name': 'Computer Science BS',
         'aliases': ['Computer Science'],
         'links': [{'collection': 'programs', 'source_key': 'academic-programs',
                    'source_record_keys': ['TAS:Computer Science BS']}]},
    ]
    data._artifacts['course-subjects'] = SUBJECTS
    data._artifacts['courses'] = {
        'CMPS 147': {'code': 'CMPS 147', 'name': 'COMPUTER SCIENCE I',
                     'description': 'Programming courses begin here.', 'credits': 4},
        # A renumbered course whose own code no longer starts with the subject's code.
        'CMPS 148': {'code': 'CMPT 148' if renumbered else 'CMPS 148',
                     'name': 'COMPUTER SCIENCE II', 'description': 'Security and design.',
                     'credits': 4},
        'ARHT 101': {'code': 'ARHT 101', 'name': 'ART HISTORY SURVEY', 'description': 'Art.'},
        'ARTS 115': {'code': 'ARTS 115', 'name': 'DRAWING', 'description': 'Studio art.'},
        'READ 101': {'code': 'READ 101', 'name': 'COLLEGE READING', 'description': 'Reading.'},
        'ACCT 100': {'code': 'ACCT 100', 'name': 'ACCOUNTING', 'description':
                     'Courses to read before CS or computer science.'},
    }
    data._artifacts['catalog-course-identities'] = None
    return data


def search(data: Any, text: str) -> dict[str, Any]:
    result: dict[str, Any] = data.search(SearchQuery(collection='courses', query=text, limit=20))
    return result


def codes(result: dict[str, Any]) -> list[str]:
    return sorted(record['fields']['code'] for record in result['records'])


@pytest.mark.parametrize('text,expected,remaining', [
    ('CS courses', [('CMPS', 'CS', 'search_term')], ''),
    ('comp sci classes about security', [('CMPS', 'comp sci', 'search_term')], 'about security'),
    ('computer science courses', [('CMPS', 'computer science', 'catalog_name')], ''),
    # The longest name wins: "art history" is ARHT before "art" can be ARTS.
    ('art history courses', [('ARHT', 'art history', 'catalog_name')], ''),
    ('art courses', [('ARTS', 'art', 'catalog_name')], ''),
    # A code matches only as written in capitals.
    ('READ courses', [('READ', 'READ', 'code')], ''),
    ('courses to read', [], 'to read'),
    ('CMPS 147', [('CMPS', 'CMPS', 'code')], '147'),
    ('CS and art courses', [('CMPS', 'CS', 'search_term'), ('ARTS', 'art', 'catalog_name')],
     'and'),
    # A name the catalog gives several codes means all of them.
    ('interdisciplinary studies', [('AIID', 'interdisciplinary studies', 'catalog_name'),
                                   ('INTD', 'interdisciplinary studies', 'catalog_name')], ''),
])
def test_a_question_names_subjects_by_code_name_or_short_form(
    text: str, expected: list[tuple[str, str, str]], remaining: str,
) -> None:
    mentions, rest = resolve_subjects(text, SUBJECTS)
    assert [(m.code, m.matched, m.basis) for m in mentions] == expected
    assert rest == remaining
    assert resolve_subjects(text, None) == ([], text)


def test_a_course_subject_is_the_capitalized_code_in_front_of_the_number() -> None:
    assert course_subject('CMPS 147') == 'CMPS'
    assert course_subject('PATHCA 101') == 'PATHCA'
    assert course_subject('cmps 147') is None
    assert course_subject(None) is None


def test_course_search_selects_the_named_subject_and_ranks_by_the_rest() -> None:
    data = subject_data()
    result = search(data, 'CS courses')
    assert codes(result) == ['CMPS 147', 'CMPS 148']
    assert result['total_matches'] == 2
    assert result['coverage']['subject_resolution'] == [
        {'code': 'CMPS', 'subject': 'Computer Science (CMPS)', 'matched': 'CS',
         'basis': 'search_term'}]
    # Other words only rank the subject's courses; none is dropped for missing them.
    ranked = search(data, 'CS security')
    assert [r['fields']['code'] for r in ranked['records']] == ['CMPS 148', 'CMPS 147']
    assert codes(search(data, 'art history courses')) == ['ARHT 101']
    assert codes(search(data, 'READ courses')) == ['READ 101']


def test_without_a_named_subject_or_the_artifact_search_is_unchanged() -> None:
    data = subject_data()
    plain = search(data, 'courses to read')
    assert 'subject_resolution' not in plain['coverage']
    assert 'ACCT 100' in codes(plain)
    data = subject_data()
    data._artifacts['course-subjects'] = None
    older = search(data, 'CS courses')
    assert 'subject_resolution' not in older['coverage']
    assert 'ACCT 100' in codes(older)


def test_lookup_finds_a_subject_by_code_while_its_name_keeps_finding_the_program() -> None:
    data = subject_data()
    by_code = data.lookup_profile(ProfileQuery(entity='CMPS', include=['subject']))
    assert by_code['resolution']['entity']['id'] == SUBJECT
    assert by_code['components']['subject']['evidence_ids'] == ['subjects:CMPS']
    record = next(r for r in by_code['records'] if r['id'] == 'subjects:CMPS')
    assert record['fields']['display_name'] == 'Computer Science (CMPS)'
    assert record['collected_at'] == '2026-08-27T14:22:13.103000+00:00'  # The list's capture time.
    by_name = data.lookup_profile(ProfileQuery(entity='Computer Science', include=['program']))
    assert by_name['resolution']['status'] == 'matched'
    assert by_name['resolution']['entity']['id'] == PROGRAM


def test_a_subject_lists_its_courses_only_while_their_own_codes_agree() -> None:
    related = subject_data().lookup_profile(
        ProfileQuery(entity_id=UUID(SUBJECT), include=['related']))['components']['related']
    assert [r['type'] for r in related['relationships']] == ['includes_course'] * 2
    # Each course is both the evidence and the target, and is returned once.
    assert related['evidence_ids'] == ['courses:CMPS 147', 'courses:CMPS 148']
    renumbered = subject_data(renumbered=True).lookup_profile(
        ProfileQuery(entity_id=UUID(SUBJECT), include=['related']))['components']['related']
    assert [r['target_record']['source_record_key'] for r in renumbered['relationships']] == [
        'CMPS 147']
    assert renumbered['unverified_relationships'] == 1


def test_a_subject_course_is_established_only_by_the_course_code() -> None:
    relationship = includes('CMPS 147')
    relationship['evidence'][0]['field'] = 'name'
    with pytest.raises(ValidationError, match="course's own code"):
        IdentityRelationship.model_validate(relationship)
