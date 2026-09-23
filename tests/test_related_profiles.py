"""The related section follows published relationships both ways and rechecks evidence."""

from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from rockygpt_brain.retrieval.profiles import ProfileQuery
from test_profile_sections import FACULTY_KEY, PERSON, PROGRAM, person_data, program_data


def related(data: Any, entity: str, **query: Any) -> dict[str, Any]:
    output: dict[str, Any] = data.lookup_profile(
        ProfileQuery(entity_id=UUID(entity), include=["related"], **query)
    )
    return output


def test_convener_is_followed_in_both_directions_with_its_catalog_evidence() -> None:
    incoming = related(program_data(), PERSON, relationship="convener", direction="incoming")
    component = incoming["components"]["related"]
    assert component["status"] == "available"
    assert [(r["type"], r["direction"], r["entity"]) for r in component["relationships"]] == [
        ("convener", "incoming", {"id": PROGRAM, "name": "Computer Science", "kind": "program"}),
    ]
    assert component["relationships"][0]["evidence_ids"] == ["programs:program:convener"]
    evidence = next(r for r in incoming["records"] if r["id"] == "programs:program:convener")
    assert any("not a verified current appointment" in text for text in evidence["limitations"])
    assert component["limitations"] == [
        "A catalog Convener field is not a verified current appointment."
    ]
    outgoing = related(program_data(), PROGRAM)["components"]["related"]
    assert [(r["direction"], r["entity"]["id"]) for r in outgoing["relationships"]] == [
        ("outgoing", PERSON),
    ]


def test_relationships_whose_evidence_no_longer_supports_them_are_not_returned() -> None:
    data = program_data()
    data._artifacts["catalog-conveners"]["programs"][0]["customFields"].pop("rJQmj")
    component = related(data, PERSON)["components"]["related"]
    assert component["relationships"] == []
    assert component["unverified_relationships"] == 1
    assert component["relationships_missing"] == 1
    assert component["status"] == "missing"


def test_filters_belong_to_the_related_section_and_fan_out_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ProfileQuery(entity_id=UUID(PERSON), include=["contact"], relationship="convener")
    with pytest.raises(ValidationError):
        ProfileQuery(entity_id=UUID(PERSON), include=["contact"], direction="incoming")
    data = program_data()
    entities = data._artifacts["campus-identities"]["entities"]
    for index in range(25):
        program = copy.deepcopy(entities[1])
        program.update(id=str(UUID(int=index + 1)), name=f"Program {index}")
        program["links"][0]["source_record_keys"] = [f"School:Program {index}"]
        entities.append(program)
    component = related(data, PERSON, direction="incoming")["components"]["related"]
    assert component["relationship_candidates"] == 26
    assert component["unexamined_relationship_candidates"] == 6
    assert component["truncated"] is True
    assert related(data, PERSON, direction="outgoing")["components"]["related"][
        "relationship_candidates"] == 0


def test_profile_course_returns_the_catalog_record_as_an_undated_listing() -> None:
    data = person_data()
    data._artifacts["campus-identities"]["entities"][0]["relationships"] = [{
        "type": "profile_course",
        "target_record": {"collection": "courses", "source_key": "academic-programs",
                          "source_record_key": "COMP 101"},
        "evidence": [{"collection": "faculty", "source_key": "faculty",
                      "source_record_key": FACULTY_KEY, "field": "courses"}],
    }]
    output = related(data, PERSON, relationship="profile_course")
    item = output["components"]["related"]["relationships"][0]
    assert item["direction"] == "outgoing" and "entity" not in item
    assert item["target_record"]["source_record_key"] == "COMP 101"
    course = next(r for r in output["records"] if r["id"] in item["target_evidence_ids"])
    assert course["collection"] == "courses"
    assert any("not a current teaching assignment" in text for text in course["limitations"])


LISTING = 'programs:program:program_faculty'


def listed(data: Any) -> Any:
    """The program's catalog Program Faculty field lists the person."""
    program = data._artifacts['campus-identities']['entities'][1]
    program['relationships'].append({
        'type': 'listed_faculty', 'target_entity_id': PERSON,
        'evidence': [{'collection': 'programs', 'source_key': 'academic-programs',
                      'source_record_key': 'School:Computer Science',
                      'field': 'customFields.xiQxl', 'source_url': 'https://example.edu/computing'}],
    })
    data._artifacts['catalog-conveners']['programs'][0]['customFields']['xiQxl'] = (
        '<p><a href="https://example.edu/faculty/ada">Ada Example</a></p>')
    return data


def test_program_faculty_listing_is_followed_both_ways_but_is_not_program_content() -> None:
    data = listed(program_data())
    outgoing = related(data, PROGRAM, relationship='listed_faculty')
    component = outgoing['components']['related']
    assert [(r['type'], r['direction'], r['entity']['id']) for r in component['relationships']] == [
        ('listed_faculty', 'outgoing', PERSON),
    ]
    assert component['relationships'][0]['evidence_ids'] == [LISTING]
    evidence = next(r for r in outgoing['records'] if r['id'] == LISTING)
    assert set(evidence['fields']['customFields']) == {'xiQxl'}
    assert '_relationship_evidence_only' not in evidence
    assert any('not a convenership' in text for text in evidence['limitations'])
    incoming = related(data, PERSON, relationship='listed_faculty', direction='incoming')
    assert [(r['direction'], r['entity']['id'])
            for r in incoming['components']['related']['relationships']] == [('incoming', PROGRAM)]
    for section in ('program', 'conveners'):
        output = data.lookup_profile(ProfileQuery(entity_id=UUID(PROGRAM), include=[section]))
        assert LISTING not in output['components'][section]['evidence_ids']
        assert [r['type'] for r in output['components'][section]['relationships']] == ['convener']


def test_a_listing_is_rechecked_and_must_cite_the_program_faculty_field() -> None:
    data = listed(program_data())
    del data._artifacts['catalog-conveners']['programs'][0]['customFields']['xiQxl']
    component = related(data, PROGRAM, relationship='listed_faculty')['components']['related']
    assert (component['relationships'], component['unverified_relationships']) == ([], 1)
    data = listed(program_data())
    evidence = data._artifacts['campus-identities']['entities'][1]['relationships'][-1]['evidence']
    evidence[0]['field'] = 'customFields.rJQmj'
    output = related(data, PROGRAM)
    assert (output['status'], output['reason']) == ('unavailable', 'invalid_identity_registry')

