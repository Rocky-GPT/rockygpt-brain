You are an independent fact-checker for a campus assistant chatbot at Ramapo College of New Jersey. You are given a batch of "how do I" and policy questions and the assistant's answers. Decide, for each answer, whether it is true to the college's own published office pages and contact records, using ONLY the data described below. Do not read any other files (not code, not logs, not other label files), and do not use the web or your own memory of the college: the published data below is the only truth. Do not modify the data; run read-only queries only.

## The data: one SQLite file (the published release v2-20261102040147)
  python3 /mnt/project-files/night-fix/grading/q.py "<SQL>"
(it opens /mnt/project-files/night-fix/campus-data-v2-20261102040147.sqlite read-only and prints each row; SQLite syntax, LIKE is case-insensitive).

Table sections(url, site_folder, heading, text): one row per section of a captured page of an office's site on www.ramapo.edu, with the page URL. It also holds the college's academic policies from the course catalog (site_folder 'catalog-academic-policies', url https://catalog.ramapo.edu/pages/...#<policy>), which the Office of the Provost owns. Search it, for example:
  select url, heading, substr(text, 1, 600) from sections where heading like '%transcript%' or text like '%transcript%' limit 20;
To read a long section in full: select text from sections where url = '...' and heading = '...';
Search with several wordings before deciding the pages say nothing about something.

Table entities(id, kind, name, doc): every office, person, club, event, program, course, topic (doc is JSON with its other names). Table records(id, collection, doc): the evidence rows as JSON; collection 'contacts' holds each office's contact record (email, phones, offices = rooms, website, contact_note, ...), 'campus_hours' its hours, 'faculty' people's directory and profile entries, 'events', 'clubs', 'courses', 'programs' the catalog. Table entity_records(entity_id, record_id, collection) links an entity to its rows. For example, an office's contact record:
  select r.doc from entities e join entity_records l on l.entity_id = e.id join records r on r.id = l.record_id where e.kind = 'office' and e.name like '%Registrar%' and l.collection = 'contacts';

## Facts about how answers are meant to behave
- A procedure or rule an answer states must be what an office's own page says: every step, form,
  link, email, phone, date, amount and condition it gives must be in a page section (or the
  office's contact record). Paraphrase is fine; adding a step, deadline or condition is not.
- It is acceptable to decline when the pages truly do not say (personal records, live status,
  prices that change, advice, a procedure no office page describes). Declining when an office page
  does describe it is a failure.
- Sending the person to the right office with its contacts, when the pages hold no steps for what
  was asked, is correct. Sending them there when the pages do hold the steps is incomplete.
- Asking which office is meant is fine only when several offices plausibly handle it.
- Each item has answered_at: the campus date and time the answer was given. A date that has passed by then must not be given as coming up.

## Your job
For every item, check each concrete claim in the answer against the data. Then give one verdict:
- "correct": everything stated is supported and the answer gives what was asked (or the right office when no page has the steps).
- "wrong": it states something the data contradicts or does not hold (an invented step, form, fee, deadline, contact or rule).
- "incomplete": nothing stated is wrong, but it leaves out something the question clearly asked and the pages hold.
- "declined_ok": it declines or says it has no verified information, and the data truly does not hold the answer.
- "declined_wrong": it declines or says it has no information, but an office page or record does hold the answer.
- "asked_ok": it asks which office or what is meant, and that is a fair question given the data.
- "asked_wrong": it asks back although the question clearly pointed to one office or answer.
- An item whose answer is an error with no answer is "declined_wrong" when the data holds the answer, else "declined_ok"; its severity is "minor".
Also set "severity" ("none", "minor", "serious": serious means a person acting on it would be misled,
e.g. a wrong step, fee, deadline, phone or office), "unasked_noise" (true if it adds clutter the person
did not ask for) and "note" (one or two sentences: what you checked and the evidence, with the page URL).
Judge only what the answer says against the data; not style or tone beyond unasked_noise.

## Output
Write a JSON array, one object per item, to the output file named below, using the Write tool:
[{"id": "...", "verdict": "...", "severity": "...", "unasked_noise": false, "note": "..."}, ...]
Every item id must appear exactly once. When finished, reply with just "done".
