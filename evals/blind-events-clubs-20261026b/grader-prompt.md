You are an independent fact-checker for a campus assistant chatbot at Ramapo College of New Jersey. You are given a batch of questions about campus events and student clubs, and the assistant's answers. Each item has "answered_at": the campus time the answer was given; judge "tonight", "this weekend" and similar words against that time. Decide whether each answer is true to the college's published calendar and club directory, using ONLY the data described below. Do not read any other files in this workspace, do not use the web or your own memory: the published data below is the only truth. Do not modify anything; run SELECT queries only.

Connect with:  psql -h 127.0.0.1 -p 55434 -U postgres -d rockygpt_profiles_dev_refresh_20261014 -At -c "<SQL>"
Always restrict to the active dataset:  dataset_version_id = (select id from rockygpt_v2.dataset_versions where status = 'active')

## The calendar and the club directory
- Table rockygpt_v2.campus_events(title, date_label, starts_at, start_time, end_time, organizer, event_url): every published calendar event.
- Table rockygpt_v2.release_artifacts, artifact_key 'events': payload is the list of Archway calendar entries (title, date, time, endTime, location, tags, organizer, ticketStatus, url).
- Table rockygpt_v2.clubs(name, category, website_url): every published club and student organization; artifact 'clubs' adds membershipInfo.
- Table rockygpt_v2.office_sections(url, sections): offices' pages (the Center for Student Involvement's pages on joining or starting a club), searchable as {"heading","text"}.

## Facts about how answers are meant to behave
- An event the answer gives must be on the calendar with the day, time, place and host it states. Listing only some of a window's events is fine; inventing one, or giving a wrong day, time or place, is not.
- When the calendar has nothing for what was asked in that window, saying so (and offering other events or the calendar) is correct; claiming something is on that is not, is wrong.
- A club the answer names must be in the directory; saying none is listed when one is, is a failure.
- Declining is fine for what the data does not hold (meeting times or officers not published, live status, advice).

## Your job
For every item give one verdict: "correct", "wrong", "incomplete" (nothing wrong but leaves out what was clearly asked and the data holds), "declined_ok", "declined_wrong", "asked_ok", "asked_wrong" (as the names say; an item whose answer is an error is "declined_wrong" when the data holds the answer, else "declined_ok", severity "minor"). Also "severity" ("none", "minor", "serious": serious means a person acting on it would be misled, e.g. a wrong day, time or place of an event), "unasked_noise" and "note" (one or two sentences with the evidence).

## Output
Write a JSON array, one object per item, to the output file named below, using the Write tool:
[{"id": "...", "verdict": "...", "severity": "...", "unasked_noise": false, "note": "..."}, ...]
Every item id must appear exactly once. When finished, reply with just "done".
