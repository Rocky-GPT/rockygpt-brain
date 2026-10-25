"""Faculty and staff lookup: the people a question names (people.py), read through the reader."""

from rockygpt_brain.people import (
    asks_leadership,
    asks_people,
    asks_people_list,
    asks_people_topic,
    by_meaning,
    computed_answers,
    find_people,
    leaders,
    listed,
    narrow,
    schools_of,
)
from rockygpt_brain.retrieval.entity_facts import MemoryEntityFacts
from test_engine import NOW

PEOPLE = [{"id": "ra", "name": "Rikki Abzug"}, {"id": "js", "name": "Jane Smith"},
          {"id": "ps", "name": "Paul Smith"}, {"id": "ys", "name": "Yolanda del Amo"}]


def test_a_person_is_named_by_their_whole_name_or_their_only_last_name() -> None:
    assert [p["id"] for p in find_people(PEOPLE, "what is rikki abzug's email")] == ["ra"]
    assert [p["id"] for p in find_people(PEOPLE, "professor abzug office hours")] == ["ra"]
    # Two people share "Smith": both are given, and the reply asks which one.
    assert [p["id"] for p in find_people(PEOPLE, "professor smith email")] == ["js", "ps"]
    assert [p["id"] for p in find_people(PEOPLE, "jane smith email")] == ["js"]
    assert [p["id"] for p in find_people(PEOPLE, "dr. del amo")] == ["ys"]
    # Degrees after the name are not typed; a hyphenated last name is one name, unique here
    # although both halves are other people's.
    more = [*PEOPLE, {"id": "sh", "name": "Joyce Shim, Ph.D., MIA, MSW"},
            {"id": "bw", "name": "Brittany A. Williams-Goldstein"},
            {"id": "lw", "name": "Lisa Williams"}, {"id": "kg", "name": "Ken Goldstein"}]
    assert [p["id"] for p in find_people(more, "what is joyce shim's email")] == ["sh"]
    assert [p["id"] for p in find_people(more, "professor shim office")] == ["sh"]
    assert [p["id"] for p in find_people(more, "prof williams-goldstein email")] == ["bw"]
    assert [p["id"] for p in find_people(more, "professor goldstein")] == ["bw", "kg"]
    # A two-letter last name only after a title word.
    xu = [*PEOPLE, {"id": "xu", "name": "Yan Xu"}]
    assert [p["id"] for p in find_people(xu, "professor xu email")] == ["xu"]
    assert find_people(xu, "xu email") == []
    # A near spelling of a last name is offered as a near spelling, never as the person asked
    # for; a common question word ("room") is not read as a name, and a first name used
    # picks among near spellings.
    near = [*PEOPLE, {"id": "jp", "name": "John Peffer"}, {"id": "mh", "name": "Mark Howenstein"},
            {"id": "rr", "name": "Rebecca Root"}]
    found = find_people(near, "office hours for professor Pefer?")
    assert [(p["id"], p.get("near_spelling")) for p in found] == [("jp", True)]
    assert [p["id"] for p in find_people(
        near, "phone number and office room for Mark Howenstien")] == ["mh"]
    assert find_people(near, "which room is the professor in") == []
    # A last name alone, in a question about no person, names no one.
    good = [*PEOPLE, {"id": "jn", "name": "Julie Norflus-Good"}]
    assert find_people(good, "is the food good at the dining hall") == []
    assert [p["id"] for p in find_people(good, "what is good's email")] == ["jn"]
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


def test_a_list_question_gives_everyone_in_the_field_named() -> None:
    directory = [
        {"id": "a", "name": "Kathleen Burke", "title": "Professor of Nursing",
         "school": "School of Science, Nursing, and Health", "email": "kmburke@ramapo.edu"},
        {"id": "b", "name": "Julie Fitzgerald", "title": "Associate Professor of Nursing",
         "school": "School of Science, Nursing, and Health", "email": "jfitzge3@ramapo.edu"},
        {"id": "c", "name": "Pradeep Bagga", "title": "Professor of Biology",
         "school": "School of Science, Nursing, and Health", "email": "pbagga@ramapo.edu"}]
    question = "can I get a list of everyone in the nursing faculty with their emails"
    assert asks_people_list(question)
    assert [p["name"] for p in listed(directory, question)] == ["Kathleen Burke",
                                                                "Julie Fitzgerald"]
    assert listed(directory, "list all faculty") == []



DIRECTORY = [
    {"id": "da", "name": "Lawrence D’Antonio", "title": "Professor of Mathematics",
     "school": "School of Science, Nursing, and Health", "office": "G-128E", "n": 0},
    {"id": "rc", "name": "Renée Cornell", "title": "Assistant Professor of Management",
     "school": "Anisfield School of Business", "email": "rcornel1@ramapo.edu", "n": 0},
    {"id": "mc", "name": "Michael Cornell", "title": "Professor of Accounting",
     "school": "Anisfield School of Business", "email": "mcornell@ramapo.edu", "n": 0},
    {"id": "jc", "name": "Jane Cornell", "title": "Professor of Chemistry",
     "school": "School of Science, Nursing, and Health", "n": 0},
    {"id": "cm", "name": "Catalin Martin", "title": "Professor of Physics",
     "school": "School of Science, Nursing, and Health",
     "office_hours": ["Office Hours: MR 1:00-2:00"], "n": 0},
    {"id": "bc", "name": "Benny Chan", "title": "Dean of SNH / Professor of Chemistry",
     "school": "School of Science, Nursing, and Health", "n": 0},
    {"id": "kb", "name": "Kathleen M. Burke",
     "title": "Assistant Dean of Nursing Programs / Professor of Nursing",
     "school": "School of Science, Nursing, and Health", "n": 0},
    {"id": "pb", "name": "Paramjeet Bagga", "title": "Professor of Biology",
     "school": "School of Science, Nursing, and Health", "n": 0},
    {"id": "sh", "name": "Susan Hangen",
     "title": "Dean School of Social Sciences and Social Work/Professor of Anthropology",
     "school": "School of Social Sciences and Social Work", "n": 0},
    {"id": "kr", "name": "Kathleen Ray", "title": "Interim Dean and Director of MSW Program",
     "school": "School of Social Sciences and Social Work", "n": 0}]
OFFICES = [{"name": "School of Science, Nursing, and Health Office", "aliases": ["SNH Office"]},
           {"name": "Anisfield School of Business", "aliases": ["ASB"]},
           {"name": "School of Social Sciences and Social Work Office",
            "aliases": ["SSSW Office"]}]


def test_names_are_matched_as_typed_without_accents_apostrophes_or_possessives() -> None:
    names = [{"id": p["id"], "name": p["name"]} for p in DIRECTORY]
    assert [p["id"] for p in find_people(names, "where is professor dantonio's office")] == ["da"]
    assert [p["id"] for p in find_people(names, "I heard Renee Cornell is good")] == ["rc"]
    assert [p["id"] for p in find_people(names, "whats professor martins email")] == ["cm"]


def test_a_shared_last_name_is_narrowed_by_the_school_or_field_asked() -> None:
    schools = schools_of(DIRECTORY, OFFICES)
    cornells = [{**p, "n": k + 1} for k, p in enumerate(DIRECTORY) if "Cornell" in p["name"]]
    assert [p["id"] for p in narrow(cornells, "prof cornell from the business school email",
                                    schools)] == ["rc", "mc"]
    assert [p["id"] for p in narrow(cornells, "professor cornell in accounting",
                                    schools)] == ["mc"]
    (fact,) = computed_answers(cornells, "professor cornell email", DIRECTORY)
    assert fact["fact"].startswith("3 people in the directory share the name asked") \
        and fact["fact"].endswith("Ask which one the student means.")


def test_the_fields_asked_are_stated_or_said_to_be_unpublished() -> None:
    martin = [{**DIRECTORY[4], "n": 3}]
    facts = [f["fact"] for f in computed_answers(
        martin, "my son's chemistry professor martin, what are his office hours and email",
        DIRECTORY)]
    assert facts == [
        "Catalin Martin's directory title is Professor of Physics, in the School of Science, "
        "Nursing, and Health.",
        "Catalin Martin's title does not name chemistry.",
        "Neither the directory nor Catalin Martin's faculty profile publishes an email address.",
        "Catalin Martin's office hours, on the faculty profile, as written: Office Hours: MR "
        "1:00-2:00."]


def test_deans_and_chairs_come_from_directory_titles() -> None:
    schools = schools_of(DIRECTORY, OFFICES)
    assert schools["School of Science, Nursing, and Health"] >= {"snh", "nursing"}
    lines = [line for line, _ in leaders(DIRECTORY, "who is the dean of sssw", schools)]
    assert lines[0].startswith("The dean of the School of Social Sciences and Social Work, by "
                               "the directory, is Susan Hangen")
    assert lines[1].startswith("Kathleen Ray is also listed in the directory, as Interim Dean")
    nursing = [line for line, _ in leaders(DIRECTORY, "who is the chair of nursing", schools)]
    assert nursing == ["Kathleen M. Burke's directory title names nursing with a leadership "
                       "role: Assistant Dean of Nursing Programs / Professor of Nursing."]
    biology = [line for line, _ in leaders(DIRECTORY, "biology department chair", schools)]
    assert biology[:2] == [
        "No directory title names a chair, head, convener, director or dean for biology.",
        "The directory's biology faculty are in the School of Science, Nursing, and Health."]
    assert biology[2].startswith("The dean of the School of Science, Nursing, and Health, by "
                                 "the directory, is Benny Chan")
    assert asks_leadership("who's the dean of the business school")
    assert not asks_leadership("dean of students office hours")
