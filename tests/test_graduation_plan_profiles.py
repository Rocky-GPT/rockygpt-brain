"""A program's recommended graduation plans: one admission cohort at a time, delivered whole."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from rockygpt_brain.campus.progress import ProgressUpdate
from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.retrieval.profiles import (
    PLAN_LIMITATION,
    PLAN_SUMMARY_LIMITATION,
    ProfileQuery,
)
from test_engine import answer, review, tools
from test_profiles import NOW, repository

PROGRAM = '846f1609-bf42-4b3e-af21-857cdf581f07'
# Longer than an ordinary 2,000-character excerpt, which would drop the later semesters.
SEMESTERS = '\n'.join(
    f'Semester {number}: CMPS {100 + number} - A COURSE WITH ITS PUBLISHED TITLE (4 credits)'
    for number in range(1, 41)
)


def plan(key: str, name: str, cohort: str, credits: int, **fields: Any) -> dict[str, Any]:
    return {'id': key, 'name': name, 'cohort': cohort, 'variantOf': None,
            'url': f'https://www.ramapo.edu/plans/{key}/',
            'applicability': f'Applicable to students admitted in {cohort}.',
            'totalCredits': credits, 'graduateCredits': None, 'gpa': '2.0',
            'totals': [f'Total Credits Required: {credits} credits'], 'planText': SEMESTERS,
            'placementText': None, 'generalEducationText': None, 'notes': [], 'documents': [],
            'limitations': [], **fields}


def plan_data() -> Any:
    data = repository()
    data.sources['graduation-plans'] = {**data.sources['directory'], 'id': 'graduation-plans',
                                        'source_key': 'graduation-plans'}
    data._artifacts['graduation-plans'] = {'captured_at': '2026-09-20T02:00:00Z', 'plans': [
        plan('cs-2026', 'Computer Science', 'Fall 2026', 128),
        plan('cs-ds-2026', 'Computer Science with MS in Data Science 4+1', 'Fall 2026', 158,
             variantOf='Computer Science', graduateCredits=30),
        plan('cs-2024', 'Computer Science', 'Fall 2024', 128),
    ]}
    data._artifacts['campus-identities']['entities'].append({
        'id': PROGRAM, 'kind': 'program', 'name': 'Computer Science BS', 'aliases': [],
        'links': [{'collection': 'graduation_plans', 'source_key': 'graduation-plans',
                   'source_record_keys': ['cs-2026', 'cs-ds-2026', 'cs-2024']}]})
    return data


def profile(data: Any, **query: Any) -> dict[str, Any]:
    output: dict[str, Any] = data.lookup_profile(
        ProfileQuery(entity_id=UUID(PROGRAM), include=['graduation_plans'], **query))
    return output


def test_the_newest_cohort_arrives_with_its_own_plan_whole_and_variants_summarized() -> None:
    output = profile(plan_data())
    component = output['components']['graduation_plans']
    assert component['cohort'] == 'Fall 2026'
    assert component['cohort_selection'] == 'newest_published'
    assert component['available_cohorts'] == ['Fall 2026', 'Fall 2024']
    assert component['available_plans'] == [
        'Computer Science', 'Computer Science with MS in Data Science 4+1']
    assert component['limitations'] == [PLAN_LIMITATION]
    own, variant = output['records']
    assert own['title'] == 'Computer Science — Fall 2026'
    assert own['fields']['planText'] == SEMESTERS and not own['content_truncated']
    # The variant is evidence that it exists, for its cohort, with its totals.
    assert variant['title'] == 'Computer Science with MS in Data Science 4+1 — Fall 2026'
    assert 'planText' not in variant['fields']
    assert (variant['fields']['totalCredits'], variant['fields']['graduateCredits']) == (158, 30)
    assert PLAN_SUMMARY_LIMITATION in variant['limitations']
    assert PLAN_SUMMARY_LIMITATION not in own['limitations']
    assert all(record['canonical_entity_id'] == PROGRAM for record in output['records'])
    # Each plan is its own record, so the variant's credits are not a conflict.
    assert component['conflicts'] == {}
    assert not any('disagree' in text for record in output['records']
                   for text in record['limitations'])


def test_a_requested_cohort_matches_its_label_or_the_published_ones_are_summarized() -> None:
    data = plan_data()
    earlier = profile(data, cohort=' fall  2024 ')
    component = earlier['components']['graduation_plans']
    assert (component['cohort'], component['cohort_selection']) == ('Fall 2024', 'requested')
    assert [record['title'] for record in earlier['records']] == ['Computer Science — Fall 2024']
    missing = profile(data, cohort='Spring 2020')
    component = missing['components']['graduation_plans']
    assert component['status'] == 'missing'
    assert component['reason'] == 'cohort_not_published'
    assert component['available_cohorts'] == ['Fall 2026', 'Fall 2024']
    # Citable evidence of what is published: each cohort's own plan, summarized.
    assert [record['title'] for record in missing['records']] == [
        'Computer Science — Fall 2026', 'Computer Science — Fall 2024']
    assert all('planText' not in record['fields'] for record in missing['records'])
    assert all(PLAN_SUMMARY_LIMITATION in record['limitations'] for record in missing['records'])


def test_a_named_plan_arrives_whole_and_an_unpublished_name_summarizes_the_cohort() -> None:
    data = plan_data()
    named = profile(data, plan='computer science with MS in Data Science 4+1')
    assert [record['title'] for record in named['records']] == [
        'Computer Science with MS in Data Science 4+1 — Fall 2026']
    assert named['records'][0]['fields']['planText'] == SEMESTERS
    unknown = profile(data, plan='Computer Science with MS in Nursing 4+1')
    component = unknown['components']['graduation_plans']
    assert (component['status'], component['reason']) == ('missing', 'plan_not_published')
    assert [record['title'] for record in unknown['records']] == [
        'Computer Science — Fall 2026', 'Computer Science with MS in Data Science 4+1 — Fall 2026']
    assert all('planText' not in record['fields'] for record in unknown['records'])


def test_a_plan_can_be_named_by_its_words_only_when_one_plan_has_them_all() -> None:
    data = plan_data()
    data._artifacts['graduation-plans']['plans'].insert(1, plan(
        'cs-am-2026', 'Computer Science with MS in Applied Mathematics 4+1', 'Fall 2026', 158,
        variantOf='Computer Science'))
    data._artifacts['campus-identities']['entities'][-1]['links'][0][
        'source_record_keys'].append('cs-am-2026')
    by_words = profile(data, plan='data science 4+1')
    assert [record['title'] for record in by_words['records']] == [
        'Computer Science with MS in Data Science 4+1 — Fall 2026']
    assert by_words['records'][0]['fields']['planText'] == SEMESTERS
    # Both variants have '4+1': the student must say which, and both are summarized.
    both = profile(data, plan='4+1')
    component = both['components']['graduation_plans']
    assert (component['status'], component['reason']) == ('missing', 'plan_ambiguous')
    assert [record['title'] for record in both['records']] == [
        'Computer Science with MS in Applied Mathematics 4+1 — Fall 2026',
        'Computer Science with MS in Data Science 4+1 — Fall 2026']
    assert all('planText' not in record['fields'] for record in both['records'])


def test_a_shared_program_name_resolves_to_the_one_program_that_publishes_plans() -> None:
    data = plan_data()
    entities = data._artifacts['campus-identities']['entities']
    entities[-1]['aliases'] = ['Computer Science']
    for key, name in [('4c8a1f35-5f0d-4a8e-9c6b-0d1e2f3a4b5c', 'Computer Science MS'),
                      ('7d9b2e46-6a1e-4b9f-8d7c-1e2f3a4b5c6d', 'Computer Science Minor')]:
        entities.append({'id': key, 'kind': 'program', 'name': name,
                         'aliases': ['Computer Science'],
                         'links': [{'collection': 'programs', 'source_key': 'catalog',
                                    'source_record_keys': [name]}]})
    # A release that also links the program's public page must still validate.
    entities[-3]['links'].append({'collection': 'major_pages', 'source_key': 'major-pages',
                                  'source_record_keys': ['cs-page']})
    output = data.lookup_profile(
        ProfileQuery(entity='Computer Science', include=['graduation_plans']))
    resolution = output['resolution']
    assert (resolution['status'], resolution['entity']['name']) == (
        'matched', 'Computer Science BS')
    assert resolution['narrowed_by'] == 'graduation_plans'
    assert [other['name'] for other in resolution['other_candidates']] == [
        'Computer Science MS', 'Computer Science Minor']
    assert output['records'][0]['title'] == 'Computer Science — Fall 2026'
    # Other sections still need the student to say which program.
    other = data.lookup_profile(ProfileQuery(entity='Computer Science', include=['program']))
    assert other['resolution']['status'] == 'ambiguous'
    assert other['resolution']['total_candidates'] == 3


@pytest.mark.parametrize('query', [
    {'include': ['program'], 'cohort': 'Fall 2026'},
    {'include': ['graduation_plans'], 'cohort': '  '},
    {'include': ['program'], 'plan': 'Computer Science'},
    {'include': ['graduation_plans'], 'plan': ' '},
])
def test_cohort_and_plan_select_only_graduation_plans(query: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProfileQuery(entity='Computer Science BS', **query)


def test_a_plan_question_is_answered_from_one_lookup_and_reviewed() -> None:
    client = Mock()
    client.create.side_effect = [
        tools(SimpleNamespace(
            type='function_call', name='lookup_profile', call_id='plans',
            arguments=json.dumps({'entity': 'Computer Science BS',
                                  'include': ['graduation_plans']}),
        )),
        answer('The Fall 2026 plan ends with CMPS 140 in its last listed semester.',
               'campus_fact', ['graduation_plans:cs-2026']),
        review('supported'),
    ]
    updates: list[ProgressUpdate] = []
    result = run_turn(
        [ChatMessage(role='user', content="What's the four-year plan for Computer Science?")],
        client=client, data=plan_data(), model='test', now=NOW, progress=updates.append,
    )
    assert result['status'] == 'answered'
    assert result['metrics']['reviewCalls'] == 1
    assert [citation['id'] for citation in result['citations']] == ['graduation_plans:cs-2026']
    [lookup] = result['trace']
    assert lookup['components']['graduation_plans']['cohort'] == 'Fall 2026'
    assert lookup['components']['graduation_plans']['available_cohorts'] == [
        'Fall 2026', 'Fall 2024']
    delivered = next(item['output'] for item in client.create.call_args_list[1].kwargs['input']
                     if isinstance(item, dict) and item.get('type') == 'function_call_output')
    assert 'Semester 40: CMPS 140' in delivered
    review_payload = json.loads(client.create.call_args_list[-1].kwargs['input'])
    coverage = review_payload['retrieval_coverage'][0]['components']['graduation_plans']
    assert coverage['cohort_selection'] == 'newest_published'
    assert {'topic': 'graduation_plans'} in [
        subject for update in updates for subject in update['subjects']]
