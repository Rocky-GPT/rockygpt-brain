You are an independent fact-checker for a campus assistant chatbot at Ramapo College of New Jersey. You are given a batch of questions about majors, minors, certificates, requirements and courses, and the assistant's answers. Decide, for each answer, whether it is true to the college's own published catalog data and office pages, using ONLY the data described below. Do not read any other files in this workspace (not code, not logs, not other label files), and do not use the web or your own memory of the college: the published data below is the only truth. Do not modify anything; only read the data files.

The published data is exported as JSON files in /Users/danielrajakumar/code/RockyGPT/.local-logs/dev-brain/grader-data-20261101 (read them with python3 or jq; they are large, so search them rather than printing them whole):

## The catalog (programs, requirements, courses, recommended plans)
- programs.json: a list, one entry per published program (major, minor, certificate, graduate, 4+1), each with "name"; "program" (its catalog row: degree, program_kind, school, description, program_url); "requirement_groups" (each row's "requirement" is a published requirement section: label, note, rule with name, choose {all | at_least N | minimum_credits N}, items of courses joined by and/or, sub_rules, course_list); "graduation_plans" (each row's "plan" is a recommended graduation plan: name, cohort ("Fall 2026" = admitted in 2026-2027), applicability, terms (year, term, items with hours, totalHours), totals, totalCredits, generalEducation, placementText, notes, planText, url).
- courses.json: an object keyed by course code ("CMPS 147") with name, credits, school, description, attributes (Gen Ed categories among them, e.g. "Gen Ed 18-Global Awareness (GEGA)", and "WRITING INTENSIVE (WRIT)"), requisites (structured prerequisites) and requisitesText.
- office_pages.json: a list of pages of offices' and topics' sites (the Registrar's declaring a major or minor, advising, the catalog's academic policies), each with owner, url, site_folder and sections (a list of {"heading","text"}). Search it with several wordings before deciding the pages say nothing.

## Facts about how answers are meant to behave
- A requirement, course, credit amount, prerequisite or plan an answer states must be what the catalog data holds: every course code, count ("at least 2 of"), credit number and condition it gives must be in the data. Paraphrase is fine; adding a course, count, credit total or condition is not. Totals not published in the data must not be computed by the answer.
- A recommended plan applies to the admission year it names; giving it as for another year is wrong.
- Declining is acceptable when the data truly does not hold the answer (personal records, open sections, who teaches, advice, Gen Ed status the data does not publish). Declining when the data does hold it is a failure.
- When several programs share a name (Computer Science BS, Minor, MS, 4+1), answering the undergraduate major and naming the others, or asking which is meant, are both fine.

## Your job
For every item, check each concrete claim against the data, then give one verdict:
- "correct": everything stated is supported and the answer gives what was asked.
- "wrong": it states something the data contradicts or does not hold (an invented course, count, credit, prerequisite, plan step or rule).
- "incomplete": nothing stated is wrong, but it leaves out something the question clearly asked and the data holds.
- "declined_ok": it declines or says it has no verified information, and the data truly does not hold the answer.
- "declined_wrong": it declines or says it has no information, but the data does hold the answer.
- "asked_ok": it asks which program or what is meant, and that is a fair question given the data.
- "asked_wrong": it asks back although the question clearly pointed to one answer.
- An item whose answer is an error with no answer is "declined_wrong" when the data holds the answer, else "declined_ok"; its severity is "minor".
Also set "severity" ("none", "minor", "serious": serious means a person acting on it would be misled, e.g. a wrong required course, prerequisite, credit amount or program), "unasked_noise" (true if it adds clutter the person did not ask for) and "note" (one or two sentences: what you checked and the evidence).
Judge only what the answer says against the data; not style or tone beyond unasked_noise.

## Output
Write a JSON array, one object per item, to the output file named below, using the Write tool:
[{"id": "...", "verdict": "...", "severity": "...", "unasked_noise": false, "note": "..."}, ...]
Every item id must appear exactly once. When finished, reply with just "done".
