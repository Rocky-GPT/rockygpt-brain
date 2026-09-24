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
from rockygpt_brain.retrieval.profiles import PLAN_LIMITATION, ProfileQuery
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


def test_plans_default_to_the_newest_cohort_and_arrive_whole() -> None:
    output = profile(plan_data())
    component = output['components']['graduation_plans']
    assert component['cohort'] == 'Fall 2026'
    assert component['cohort_selection'] == 'newest_published'
    assert component['available_cohorts'] == ['Fall 2026', 'Fall 2024']
    assert component['limitations'] == [PLAN_LIMITATION]
    assert [record['title'] for record in output['records']] == [
        'Computer Science — Fall 2026', 'Computer Science with MS in Data Science 4+1 — Fall 2026']
    assert all(record['fields']['planText'] == SEMESTERS and not record['content_truncated']
               for record in output['records'])
    assert all(record['canonical_entity_id'] == PROGRAM for record in output['records'])
    # Each plan is its own record, so the variant's credits are not a conflict.
    assert component['conflicts'] == {}
    assert not any('disagree' in text for record in output['records']
                   for text in record['limitations'])


def test_a_requested_cohort_matches_its_label_or_is_reported_unpublished() -> None:
    data = plan_data()
    earlier = profile(data, cohort=' fall  2024 ')
    component = earlier['components']['graduation_plans']
    assert (component['cohort'], component['cohort_selection']) == ('Fall 2024', 'requested')
    assert [record['title'] for record in earlier['records']] == ['Computer Science — Fall 2024']
    missing = profile(data, cohort='Spring 2020')
    component = missing['components']['graduation_plans']
    assert missing['records'] == [] and component['status'] == 'missing'
    assert component['reason'] == 'cohort_not_published'
    assert component['available_cohorts'] == ['Fall 2026', 'Fall 2024']


@pytest.mark.parametrize('query', [
    {'include': ['program'], 'cohort': 'Fall 2026'},
    {'include': ['graduation_plans'], 'cohort': '  '},
])
def test_cohort_selects_only_graduation_plans(query: dict[str, Any]) -> None:
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
