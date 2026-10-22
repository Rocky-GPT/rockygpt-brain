"""Faculty and staff lookup: the people a question names (people.py), read through the reader."""

from rockygpt_brain.people import asks_people, asks_people_topic, by_meaning, find_people
from rockygpt_brain.retrieval.entity_facts import MemoryEntityFacts
from test_engine import NOW

PEOPLE = [{"id": "ra", "name": "Rikki Abzug"}, {"id": "js", "name": "Jane Smith"},
          {"id": "ps", "name": "Paul Smith"}, {"id": "ys", "name": "Yolanda del Amo"}]


def test_a_person_is_named_by_their_whole_name_or_their_only_last_name() -> None:
    assert [p["id"] for p in find_people(PEOPLE, "what is rikki abzug's email")] == ["ra"]
    assert [p["id"] for p in find_people(PEOPLE, "professor abzug office hours")] == ["ra"]
    # Two people share "Smith": a last name alone is not enough.
    assert find_people(PEOPLE, "professor smith email") == []
    assert [p["id"] for p in find_people(PEOPLE, "jane smith email")] == ["js"]
    assert [p["id"] for p in find_people(PEOPLE, "dr. del amo")] == ["ys"]
    assert asks_people("prof abzug email") and not asks_people("how do i get a parking permit")
    assert asks_people_topic("who teaches business ethics")
    assert not asks_people_topic("professor smith email")
    names = {"ra": "Rikki Abzug", "js": "Jane Smith"}
    assert [p["id"] for p in by_meaning([("ra", 0.49), ("js", 0.47)], names)] == ["ra", "js"]
    assert by_meaning([("ra", 0.23)], names) == []


def test_a_persons_facts_are_their_directory_entry_and_their_profile() -> None:
    contact = {"id": "c-ra", "source_key": "faculty", "source_record_key": "faculty:ra",
               "name": "Rikki Abzug", "title": "Professor of Management",
               "department": "Anisfield School of Business", "email": "rabzug@ramapo.edu",
               "phone": "(201) 684-7392", "office": "ASB-107", "collected_at": NOW,
               "freshness_sla_hours": 4320, "canonical_url": "https://www.ramapo.edu/asb/"}
    profile = {"id": "f-ra", "source_key": "faculty", "source_record_key": "page#email=x",
               "collected_at": NOW, "freshness_sla_hours": 4320,
               "profile": {"profileUrl": "https://www.ramapo.edu/asb/faculty/rikki-abzug/",
                           "bio": "Year Joined: 2006\n\nOffice Hours: Monday 10:30 AM-1:00 PM\n\n"
                                  "Rikki Abzug is a professor.",
                           "researchInterests": ["Organizational Governance"],
                           "courses": ["Ethics in Business"]}}
    service = MemoryEntityFacts(
        dataset_version="release-1", identity_hash="identities-1",
        entities=[{"id": "ra", "name": "Rikki Abzug", "kind": "person", "aliases": [],
                   "links": [{"collection": "contacts", "source_key": "faculty",
                              "source_record_keys": ["faculty:ra"]},
                             {"collection": "faculty", "source_key": "faculty",
                              "source_record_keys": ["page#email=x"]}]}],
        contacts=[contact], records={"faculty": [profile]}, now=lambda: NOW)
    (facts,) = service.get_person_facts(["ra"], "release-1", identity_hash="identities-1")
    value = facts["properties"][0]["values"][0]["value"]
    assert (value["email"], value["office"], value["title"]) == (
        "rabzug@ramapo.edu", "ASB-107", "Professor of Management")
    # Office hours are the profile's own line, as written.
    assert value["office_hours"] == ["Office Hours: Monday 10:30 AM-1:00 PM"]
    assert value["research_interests"] == ["Organizational Governance"]
    assert value["profile_url"] == "https://www.ramapo.edu/asb/faculty/rikki-abzug/"
