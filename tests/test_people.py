"""Faculty and staff lookup: the people a question names (people.py), read through the reader."""

from typing import Any

from rockygpt_brain.people import (
    about_named,
    asks_leadership,
    asks_people,
    asks_people_list,
    asks_people_topic,
    by_meaning,
    computed_answers,
    contact_text,
    find_people,
    leaders,
    listed,
    narrow,
    only_who,
    person_text,
    schools_of,
    stand_in_fact,
    stand_ins,
    teachers,
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



DIRECTORY: list[dict[str, Any]] = [
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
    assert lines[0].startswith("The dean of the School of Social Sciences and Social Work is "
                               "Susan Hangen (")
    assert lines[1].startswith("Kathleen Ray is also listed in the directory, as Interim Dean")
    nursing = [line for line, _ in leaders(DIRECTORY, "who is the chair of nursing", schools)]
    assert nursing == ["Kathleen M. Burke leads in nursing by their directory title: "
                       "Assistant Dean of Nursing Programs / Professor of Nursing."]
    biology = [line for line, _ in leaders(DIRECTORY, "biology department chair", schools)]
    assert biology[:2] == [
        "The directory lists no chair, head, convener, director or dean for biology.",
        "The directory's biology faculty are in the School of Science, Nursing, and Health."]
    assert biology[2].startswith("The dean of the School of Science, Nursing, and Health is "
                                 "Benny Chan (")
    assert asks_leadership("who's the dean of the business school")
    assert asks_people("professor abzug office hours")
    assert not asks_people("What are the Student Accounts office hours?")
    assert not asks_leadership("dean of students office hours")


def test_a_school_named_by_another_name_its_identity_has_gets_its_deans() -> None:
    # 11-02: "School of Social Science and Human Services" matched no school by its words (a tie
    # of "social" and "science"), so every dean was listed and an old page's dean was named.
    directory = [*DIRECTORY, {"id": "kg", "name": "Ken Goldstein",
                              "title": "Dean of Arts, Humanities, and Education",
                              "school": "School of Arts, Humanities, and Education", "n": 0}]
    schools = schools_of(directory, OFFICES)
    identities = [
        {"name": "School of Social Sciences and Social Work",
         "aliases": ["SSSW", "School of Social Science and Human Services"]},
        {"name": "School of Arts, Humanities, and Education",
         "aliases": ["AHE", "School of Social Science and Human Services"]},
        {"name": "School of Science, Nursing, and Health", "aliases": ["SNH"]}]
    lines = [line for line, _ in leaders(
        directory, "Who is the dean of the School of Social Science and Human Services?",
        schools, identities)]
    assert lines[0] == ('"School of Social Science and Human Services" is another name the '
                        "college's records give 2 schools: the School of Social Sciences and "
                        "Social Work and the School of Arts, Humanities, and Education.")
    assert lines[1].startswith("The dean of the School of Social Sciences and Social Work is "
                               "Susan Hangen (")
    assert any(line.startswith("The dean of the School of Arts, Humanities, and Education is "
                               "Ken Goldstein (") for line in lines)
    assert not any("Benny Chan" in line for line in lines)
    # A school's own name is no other name.
    named = [line for line, _ in leaders(
        directory, "who is the dean of the school of science, nursing, and health", schools,
        identities)]
    assert named[0].startswith("The dean of the School of Science, Nursing, and Health")


def test_ordinary_words_are_not_misspelt_names_and_two_people_can_be_named() -> None:
    names = [{"id": "ll", "name": "Lisa Lutter"}, {"id": "rm", "name": "Robert Mentore"},
             {"id": "mj", "name": "Matthew Jobrack"}, {"id": "cc", "name": "Christina Chung"},
             {"id": "ab", "name": "Alexander Biagioli"}]
    ordinary = frozenset({"letter", "mentor", "recommendation"})
    assert find_people(names, "which prof should i ask for a recommendation letter",
                       ordinary) == []
    assert find_people(names, "is there a professor who could mentor me", ordinary) == []
    assert [(p["id"], p.get("near_spelling")) for p in find_people(
        names, "matt jobrak what does he teach", ordinary)] == [("mj", True)]
    assert [p["id"] for p in find_people(
        names, "who is the better advisor, Christina Chung or Alex Biagioli?")] == ["cc", "ab"]


def test_who_teaches_a_subject_is_read_from_profile_courses() -> None:
    directory: list[dict[str, Any]] = [
        {"id": "a", "name": "Roark Atkinson", "title": "Associate Professor of History",
         "courses": ["Introduction to U.S. History I"]},
        {"id": "b", "name": "Stephen Anderson", "title": "Associate Professor of Chemistry",
         "courses": ["Organic Chemistry I: Lecture and Lab"]},
        {"id": "c", "name": "Jane Doe", "title": "Professor of History",
         "courses": ["Ancient Rome"]},
        *({"id": f"x{n}", "name": f"Person {n}", "title": "Professor of Art"} for n in range(8))]
    assert [p["id"] for p in teachers(directory, "who teaches US history?")] == ["a"]
    assert [p["id"] for p in teachers(directory, "who teaches organic chemistry")] == ["b"]


def test_a_field_only_mentioned_is_not_reported_without_a_chair() -> None:
    schools = schools_of(DIRECTORY, OFFICES)
    lines = [line for line, _ in leaders(
        [*DIRECTORY, {"id": "cp", "name": "Ann Clin", "title": "Professor of Clinical Psychology",
                      "school": "School of Social Sciences and Social Work"}],
        "who is in charge of nursing? it's about clinical placements", schools)]
    assert lines == ["Kathleen M. Burke leads in nursing by their directory title: "
                     "Assistant Dean of Nursing Programs / Professor of Nursing."]


def test_an_unusual_last_name_or_a_middle_initial_needs_no_people_word() -> None:
    names = [{"id": "jt", "name": "Jessica Terrizzi"}, {"id": "kb", "name": "Kathleen M. Burke"},
             {"id": "jn", "name": "Julie Norflus-Good"}]
    ordinary = frozenset({"good", "food", "dining", "hall", "what", "her", "title"})
    assert [p["id"] for p in find_people(
        names, "jess terrizzi what dept is she in and what's her title", ordinary)] == ["jt"]
    assert [p["id"] for p in find_people(names, "who is Kathleen Burke?", ordinary)] == ["kb"]
    assert find_people(names, "is the food good at the dining hall", ordinary) == []
    directory: list[dict[str, Any]] = [
        {"id": "r", "name": "Paul Reck", "title": "Assistant Professor of Sociology",
         "courses": ["Introduction to Sociology"]},
        *({"id": f"x{n}", "name": f"P {n}", "title": "Professor of Art"} for n in range(6))]
    assert [p["id"] for p in teachers(directory, "who teaches intro to sociology?")] == ["r"]


def test_who_a_person_is_and_the_fields_asked_are_said_neatly_by_code() -> None:
    abzug = {"n": 1, "name": "Rikki Abzug", "title": "Professor of Management",
             "school": "Anisfield School of Business", "email": "rabzug@ramapo.edu",
             "phone": "(201) 684-7392", "office": "ASB-107",
             "office_hours": ["Office Hours: Monday 10:30 AM-1:00 PM."]}
    assert only_who("Who is Professor Abzug?", abzug)
    assert not only_who("Who is Rikki Abzug's advisor?", abzug)  # Something more is asked.
    assert person_text(abzug, "Who is Rikki Abzug?") == (
        "Rikki Abzug is Professor of Management in the Anisfield School of Business. Email: "
        "rabzug@ramapo.edu. Phone: (201) 684-7392. Office: ASB-107. [1]")
    assert person_text(abzug, "What are Professor Abzug's office hours?") == (
        "Rikki Abzug is Professor of Management in the Anisfield School of Business. Office "
        "hours: Monday 10:30 AM-1:00 PM. [1]")
    assert person_text(abzug, "What does Professor Abzug teach?") == (
        "Rikki Abzug is Professor of Management in the Anisfield School of Business. The "
        "faculty profile lists no courses. [1]")
    # A name only near one in the directory is confirmed with the student first.
    assert person_text({**abzug, "near_spelling_of_asked_name": True}, "Who is Abzog?") is None
    facts = [f["fact"] for f in computed_answers([abzug], "what does abzug teach", [])]
    assert facts[-1] == "Rikki Abzug's faculty profile lists no courses."


def test_a_person_named_dean_or_a_dean_named_by_title_is_not_a_question_about_deans() -> None:
    chen, hangen = {"name": "Dean Chen"}, {"name": "Susan Hangen"}
    assert not asks_leadership(about_named("Who is Dean Chen?", [chen]))
    assert not asks_leadership(about_named("Who is Dean Hangen?", [hangen]))
    assert asks_leadership(about_named("Who is the dean of SSSW, Susan Hangen?", [hangen]))
    assert only_who("Who is Dean Hangen?", hangen) and only_who("Who is Dean Chen?", chen)


def test_who_teaches_a_subject_matches_what_people_teach_and_the_whole_phrase() -> None:
    directory = [
        {"name": "Ann Byte", "title": "Professor of Computer Science",
         "courses": ["Data Structures"]},
        {"name": "Bo Cell", "title": "Professor of Biology", "courses": ["Microbiology"],
         "research_interests": ["I collaborate with Computer Science faculty."]},
        {"name": "Cy Tune", "title": "Professor of Music", "courses": ["Computer Music"]},
        {"name": "Di Learn", "title": "Professor of Data Science",
         "research_interests": ["Machine Learning"]}]
    # A teaching question reads what people teach; the whole phrase wins over one rare word.
    assert [e["name"] for e in teachers(directory, "Who teaches computer science?")] == [
        "Ann Byte"]
    assert [e["name"] for e in teachers(directory, "Who researches machine learning?")] == [
        "Di Learn"]


def test_a_persons_directory_contacts_are_said_by_code() -> None:
    petkus = {"n": 2, "name": "Ed Petkus", "email": "epetkus@ramapo.edu",
              "phone": "(201) 684-7377", "office": "ASB-333"}
    assert contact_text(petkus) == (
        "Email: epetkus@ramapo.edu. Phone: (201) 684-7377. Office: ASB-333. [2]")
    assert contact_text({"n": 3, "name": "No Contacts"}) is None


def test_a_letter_standing_in_for_a_name_names_no_one() -> None:
    # 11-03 repeat test: "who's better for cs, professor A or professor B?" was answered every
    # time with two real computer science professors found by meaning, as if they were A and B.
    assert stand_ins("who's better for cs, professor A or professor B? be fr") == [
        "professor A", "professor B"]
    assert stand_ins("prof x or prof y for calc") == ["prof x", "prof y"]
    assert stand_ins("Professor X's class") == ["Professor X"]
    # "a" and "I" are words, and an initial before a name is part of the name.
    assert stand_ins("professor a or professor b") == ["professor b"]
    assert stand_ins("is there a professor a student can see") == []
    assert stand_ins("Is Dr. J. Smith in today?") == []
    assert stand_ins("Professor Brisson email") == []
    assert find_people(PEOPLE, "professor A or professor B") == []
    assert stand_in_fact(["Professor A", "Professor B"]) == (
        "The question names no one: Professor A and Professor B are letters standing in for "
        "names, so no person in the directory may be said to be any of them.")
    assert stand_in_fact(["Dr. X"]) == (
        "The question names no one: Dr. X is a letter standing in for a name, so no person in "
        "the directory may be said to be that person.")


TITLES = {"PSYC 101": "INTRODUCTION TO PSYCHOLOGY", "PSYC 102": "PSYCHOLOGY OF LEARNING",
          "CHEM 212": "ORGANIC CHEMISTRY I LECTURE", "CHEM 212L": "ORGANIC CHEMISTRY I LAB",
          "CHEM 213": "ORGANIC CHEMISTRY II LECTURE", "CHEM 213L": "ORGANIC CHEMISTRY II LAB",
          "BIOL 101": "INTRODUCTION TO BIOLOGY", "MATH 121": "CALCULUS I",
          "PSYC 390": "INDEPENDENT STUDY IN PSYCHOLOGY"}


def test_a_course_is_named_by_its_code_or_every_word_of_its_title() -> None:
    from rockygpt_brain.people import asks_who_teaches, course_key, courses_named
    assert course_key("ORGANIC CHEMISTRY II LECTURE") == (("organic", "chemistry"),
                                                          frozenset({2}))
    assert course_key("Organic Chemistry II (lec & lab)") == (("organic", "chemistry"),
                                                              frozenset({2}))
    assert course_key("Fundamentals of Chemistry I and II Lecture and Laboratory") == (
        ("fundamentals", "chemistry"), frozenset({1, 2}))
    assert course_key("MUSI 255 - MUSIC, HISTORY AND CULTURE (https://x.edu/a)") == (
        ("music", "history", "culture"), frozenset())
    assert courses_named("who teaches intro to psych", TITLES) == ["PSYC 101"]
    assert courses_named("which professors teach organic chemistry 2", TITLES) == [
        "CHEM 213", "CHEM 213L"]
    assert courses_named("who teaches orgo chem", TITLES) == []  # "orgo" says no title word.
    assert courses_named("who teaches psyc101", TITLES) == ["PSYC 101"]
    assert courses_named("who teaches calc 1", TITLES) == ["MATH 121"]
    assert courses_named("can i take calc", TITLES) == ["MATH 121"]  # "can i" is no Calculus I.
    assert courses_named("who does independent study in psychology", TITLES) == []
    assert asks_who_teaches("who teaches intro to psych")
    assert asks_who_teaches("which professors teach organic chemistry 2")
    assert not asks_who_teaches("what is intro to psych about")


def test_who_teaches_a_course_is_every_profile_that_lists_it() -> None:
    # Fix check 11-04: the writer listed all 12 psychology faculty for one course.
    from rockygpt_brain.people import course_teachers, course_teachers_text
    directory = [
        {"name": "Naseem Choudhury", "title": "Professor", "email": "n@x.edu",
         "courses": ["PSYC 101 - Introduction to Psychology"], "office": "G-273"},
        {"name": "Ann Ishak", "courses": ["Introduction to Psychology"]},
        {"name": "Old Prof", "school": "Social Science (Retired)", "courses": ["PSYC 101"]},
        {"name": "Chem One", "courses": ["Organic Chemistry II (lec & lab)"]},
        {"name": "Chem Two", "courses": ["Organic Chemistry I Lecture"]},
        {"name": "Chem All", "courses": ["Organic Chemistry"]},
        {"name": "Nobody", "courses": ["Psychology of Learning"]}]
    psych = course_teachers(directory, ["PSYC 101"], TITLES)
    assert [p["name"] for p in psych] == ["Naseem Choudhury", "Ann Ishak", "Old Prof"]
    orgo = course_teachers(directory, ["CHEM 213", "CHEM 213L"], TITLES)
    assert [p["name"] for p in orgo] == ["Chem One", "Chem All"]
    people = [{**p, "n": k} for k, p in enumerate(psych, start=2)]
    assert course_teachers_text(["PSYC 101"], TITLES, people, ["office"]) == (
        "Faculty profiles that list PSYC 101 Introduction to Psychology among the courses they "
        "teach:\n"
        "- Naseem Choudhury, Professor, n@x.edu, office G-273 [2]\n"
        "- Ann Ishak, no office listed [3]\n"
        "- Old Prof, listed as retired in the directory, no office listed [4]\n"
        "A profile lists the courses a professor teaches; it doesn't say who teaches which "
        "section this term.")


def test_in_residence_is_a_role_not_a_field() -> None:
    # Fix check 11-04: "who is in charge of residence life" was read as the field of the
    # directory's "Entrepreneur in Residence", and answered with the business school's dean.
    from rockygpt_brain.people import fields_asked
    directory = [{"title": "Entrepreneur in Residence"}, {"title": "Professor of Psychology"}]
    assert fields_asked("who is in charge of residence life", directory) == set()
    assert fields_asked("who is the psychology convener", directory) == {"psychology"}
