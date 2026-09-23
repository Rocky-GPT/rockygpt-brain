"""Program requirements keep the published structure; an option is never a requirement."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

from rockygpt_brain.contracts import ChatMessage
from rockygpt_brain.core.engine import run_turn
from rockygpt_brain.core.routing import ROUTED_SECTIONS
from rockygpt_brain.core.tools import tool_definitions
from rockygpt_brain.retrieval.knowledge import course_id
from rockygpt_brain.retrieval.profiles import ProfileQuery
from test_engine import answer, review, tools
from test_profile_sections import PERSON, PROGRAM, link, program_data
from test_profiles import NOW

MATH = 'a4d6f0f2-5a53-4a47-9a0c-2c7f4f1f9b10'
GEN_ED, MAJOR, ELECTIVES, LEVEL, TEXT = (str(UUID(int=n)) for n in range(1, 6))
CATALOG = 'https://example.edu/catalog/COMP'


def course(code: str, name: str | None = None, linked: bool = True) -> dict[str, Any]:
    return {'code': code, 'name': name,
            'course_id': course_id('academic-programs', code) if linked else None}


def rule(condition: str, *, count: int | None = None, choose: Any = None,
         items: tuple[tuple[str, list[dict[str, Any]]], ...] = (),
         sub_rules: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    return {'condition': condition, 'count': count, 'credits': None, 'choose': choose,
            'items': [{'logic': logic, 'courses': courses} for logic, courses in items],
            'sub_rules': list(sub_rules)}


def group(group_id: str, label: str, shape: str, **values: Any) -> dict[str, Any]:
    return {'id': group_id, 'record_type': 'requirement_group', 'label': label, 'shape': shape,
            'note': None, 'rule': None, 'course_list': None, 'program_sections': 1,
            'provenance': [], **values}


def edge(program: str, group_id: str, order: int, major: int) -> dict[str, Any]:
    return {'type': 'requirement_group', 'source': {'entity_id': program},
            'target': {'record_id': group_id}, 'order': order,
            'path': ['schools', 0, 'majors', major, 'requirements', order]}


def requirement_data() -> Any:
    data = program_data()
    data._artifacts['campus-identities']['entities'].append({
        'id': MATH, 'kind': 'program', 'name': 'Mathematics', 'aliases': [],
        'links': [link('programs', 'academic-programs', 'School:Mathematics')],
    })
    majors = data._artifacts['programs']['schools'][0]['majors']
    majors[0]['catalogUrl'] = CATALOG
    majors.append({'name': 'Mathematics', 'catalogUrl': 'https://example.edu/catalog/MATH'})
    ai = course('COMP 331', 'Artificial Intelligence')
    groups = [
        group(GEN_ED, 'General Education: Quantitative Reasoning', 'course_list',
              note='Choose one.', program_sections=2, course_list={
                  'select_count': 1, 'choose': {'at_least': 1},
                  'courses': [course('COMP 101', 'Introduction to Computing'),
                              course('MATH 121', 'Statistics')]}),
        group(MAJOR, 'Major Requirements', 'rule', rule=rule(
            'allOf', choose={'all': True}, sub_rules=(
                rule('completedAllOf', choose={'all': True}, items=(
                    ('and', [course('COMP 101', 'Introduction to Computing')]),
                    ('and', [course('COMP 240', 'Data Structures')]))),
                rule('completedAnyOf', choose={'at_least': 1}, items=(
                    ('or', [course('MATH 205', 'Calculus I'), course('MATH 237', 'Discrete')]),)),
            ))),
        group(ELECTIVES, 'Electives: Select Two (2)', 'rule', rule=rule(
            'completedAtLeastXOf', count=2, choose={'at_least': 2}, items=(
                ('and', [ai]), ('and', [course('COMP 340', 'Networks')]),
                ('and', [course('NOPE 999', linked=False)])))),
        # Published as "any of" with a count: kept, never interpreted.
        group(LEVEL, '300-Level Course', 'rule', rule=rule(
            'completedAnyOf', count=2, items=(('and', [course('COMP 311', 'Theory')]),))),
        group(TEXT, 'Catalog Requirements', 'text', note='- Two 200-level courses'),
    ]
    order = [GEN_ED, MAJOR, ELECTIVES, LEVEL, TEXT]
    edges = [edge(PROGRAM, group_id, index, 0) for index, group_id in enumerate(order)]
    data._artifacts['program-requirement-groups'] = {
        'schema_version': 1, 'source': {'artifact_key': 'programs'}, 'semantics': {},
        'groups': groups, 'unresolved': [],
        # Artifact order is not section order.
        'edges': [*reversed(edges), edge(MATH, GEN_ED, 0, 1), {
            'type': 'requirement_option', 'source': {'record_id': ELECTIVES},
            'target': {'entity_id': ai['course_id']}, 'path': ['rule', 'items', 0, 'courses', 0],
            'logic': 'and', 'code': 'COMP 331'}],
    }
    return data


def requirements(data: Any, entity: str) -> dict[str, Any]:
    output: dict[str, Any] = data.lookup_profile(
        ProfileQuery(entity_id=UUID(entity), include=['requirements']))
    return output


def test_nested_choices_stay_in_catalog_order_with_program_citations() -> None:
    output = requirements(requirement_data(), PROGRAM)
    component = output['components']['requirements']
    assert component['status'] == 'available'
    assert (component['requirement_groups'], component['shared_requirement_groups']) == (5, 1)
    records = output['records']
    assert [r['fields']['section'] for r in records] == [
        'General Education: Quantitative Reasoning', 'Major Requirements',
        'Electives: Select Two (2)', '300-Level Course', 'Catalog Requirements']
    assert records[2]['id'] == f'program_requirements:{ELECTIVES}@{PROGRAM}'
    assert {r['url'] for r in records} == {CATALOG}
    assert all(r['related_to_entity_id'] == PROGRAM for r in records)
    assert records[0]['fields']['requirement'] == {
        'select_count': 1, 'choose': {'at_least': 1},
        'options': ['COMP 101 Introduction to Computing', 'MATH 121 Statistics']}
    assert records[0]['fields']['note'] == 'Choose one.'
    assert records[1]['fields']['requirement'] == {
        'condition': 'allOf', 'choose': {'all': True}, 'parts': [
            {'condition': 'completedAllOf', 'choose': {'all': True},
             'options': ['COMP 101 Introduction to Computing', 'COMP 240 Data Structures']},
            {'condition': 'completedAnyOf', 'choose': {'at_least': 1},
             'options': ['MATH 205 Calculus I or MATH 237 Discrete']},
        ]}
    assert records[2]['fields']['requirement'] == {
        'condition': 'completedAtLeastXOf', 'count': 2, 'choose': {'at_least': 2},
        'options': ['COMP 331 Artificial Intelligence', 'COMP 340 Networks', 'NOPE 999']}
    assert records[4]['fields']['note'] == '- Two 200-level courses'
    assert all(any('not a required course on its own' in text for text in r['limitations'])
               for r in records)
    assert [any('not catalog courses' in text for text in r['limitations'])
            for r in records] == [False, False, True, False, False]
    assert [any('without interpretation' in text for text in r['limitations'])
            for r in records] == [False, False, False, True, False]
    assert records[3]['fields']['requirement'] == {
        'condition': 'completedAnyOf', 'count': 2, 'options': ['COMP 311 Theory']}


def test_a_shared_group_is_one_record_in_the_graph_but_program_scoped_evidence() -> None:
    data = requirement_data()
    computing, math = requirements(data, PROGRAM), requirements(data, MATH)
    shared = [next(r for r in output['records'] if r['fields']['requirement_group_id'] == GEN_ED)
              for output in (computing, math)]
    assert shared[0]['id'] != shared[1]['id']
    assert [r['fields']['program'] for r in shared] == ['Computer Science', 'Mathematics']
    assert [r['url'] for r in shared] == [CATALOG, 'https://example.edu/catalog/MATH']
    assert shared[0]['fields']['requirement'] == shared[1]['fields']['requirement']
    assert data._seen[shared[0]['id']]['fields']['program'] == 'Computer Science'
    assert len(math['records']) == 1


def test_catalog_rule_text_and_constraints_survive_profile_evidence() -> None:
    data = requirement_data()
    published = data._artifacts['program-requirement-groups']['groups'][1]
    published['rule'] = rule('catalogBlock', sub_rules=({
        **rule('freeformText'), 'name': 'Adviser approval',
        'text': 'Select a concentration with your adviser.',
        'note': 'At least one course must be at the 300 level.',
        'constraints': {'minCourses': 2, 'minCredits': 8},
    },))
    record = requirements(data, PROGRAM)['records'][1]
    assert record['fields']['requirement']['parts'] == [{
        'condition': 'freeformText', 'name': 'Adviser approval',
        'text': 'Select a concentration with your adviser.',
        'note': 'At least one course must be at the 300 level.',
        'constraints': {'minCourses': 2, 'minCredits': 8},
    }]
    assert any('without interpretation' in text for text in record['limitations'])


def test_citation_follows_the_exact_published_path_never_names() -> None:
    data = requirement_data()
    data._artifacts['campus-identities']['entities'][1]['name'] = 'Renamed Program'
    assert {r['url'] for r in requirements(data, PROGRAM)['records']} == {CATALOG}
    for path in (['schools', 0, 'majors', 9, 'requirements', 0],
                 ['schools', True, 'majors', 0, 'requirements', 0], None):
        data = requirement_data()
        data._artifacts['program-requirement-groups']['edges'][-2]['path'] = path
        record = requirements(data, MATH)['records'][0]
        assert record['url'] == 'https://example.edu/academic-programs'


def test_requirements_apply_to_programs_and_report_what_is_not_published() -> None:
    data = requirement_data()
    person = requirements(data, PERSON)['components']['requirements']
    assert (person['status'], person['reason']) == ('missing', 'requirements_apply_to_programs')
    data._artifacts['program-requirement-groups']['edges'].append(
        edge(PROGRAM, str(UUID(int=99)), 9, 0))
    partial = requirements(data, PROGRAM)['components']['requirements']
    assert (partial['status'], partial['missing_requirement_groups']) == ('partial', 1)
    assert partial['relationships_missing'] == 1
    data._artifacts['program-requirement-groups'] = None
    missing = requirements(data, PROGRAM)['components']['requirements']
    assert (missing['status'], missing['reason']) == (
        'missing', 'requirement_groups_not_published')


def test_option_question_uses_model_tool_choice_and_generated_answer_review() -> None:
    assert 'requirements' not in ROUTED_SECTIONS
    profile = next(t for t in tool_definitions() if t['name'] == 'lookup_profile')
    assert 'requirements' in json.dumps(profile['parameters'])
    data = requirement_data()
    client = Mock()
    electives = f'program_requirements:{ELECTIVES}@{PROGRAM}'
    client.create.side_effect = [tools(SimpleNamespace(
        type='function_call', name='lookup_profile', call_id='profile0',
        arguments=json.dumps({'entity': 'Computer Science', 'include': ['requirements']}),
    )), answer(
        'No. COMP 331 Artificial Intelligence is one option in Electives: Select Two (2); '
        'the program requires at least two courses from that list.',
        'campus_fact', [electives],
    ), review('supported')]
    result = run_turn([ChatMessage(
        role='user', content='Is COMP 331 required for the Computer Science program?',
    )], client=client, data=data, model='test', now=NOW)
    assert result['status'] == 'answered'
    assert result['metrics']['reviewCalls'] == 1
    component = result['trace'][0]['components']['requirements']
    assert (component['status'], len(component['evidence_ids'])) == ('available', 5)
    delivered = json.loads(next(
        item['output'] for item in client.create.call_args_list[1].kwargs['input']
        if isinstance(item, dict) and item.get('type') == 'function_call_output'))
    assert delivered['components']['requirements']['requirement_groups'] == 5
    assert 'not a required course' in json.dumps(delivered['evidence_groups'])
    reviewed = client.create.call_args_list[-1].kwargs['input']
    assert 'COMP 331 Artificial Intelligence' in reviewed and 'not a required course' in reviewed
