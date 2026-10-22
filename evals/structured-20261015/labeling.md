# Labeling instructions (given to every labeler as is)

You are writing the answer key for questions to a Ramapo College campus chatbot. Each case has the
student's message, what the question is about (`kind`, `entity`, `ask`, and a `day`/`time` when
given), and `sources`: the text of Ramapo's own published pages for it, captured on 2026-10-15
(dining hours: the Sodexo hours page rendered that day). Use ONLY those sources. Do not browse and
do not use outside knowledge.

For each case write:
- `published`: true when the sources give the answer, false when they do not.
- `accept`: when published, every value a correct reply may give, each as short as possible and
  written as on the page (an email, a phone number such as "(201) 684-7695", a room such as
  "ASB-424", hours such as "8:30 AM - 4:30 PM", a departure time such as "10:30 AM"). A correct
  reply must contain at least one of them. Include alternatives only when the page itself gives
  more than one valid answer (for example two main phone lines). Empty when not published.
- `expected`: one sentence saying what a correct reply says. When not published: say so, e.g.
  "The pages do not publish a fax number for X."
- `soft`: true when reasonable people could disagree on the right answer: the student's message
  could mean something else than `ask`, the pages conflict, or the answer depends on a date the
  pages do not settle (a holiday, a season). Otherwise false.
- `note`: required when `soft` is true or `published` is false: what you checked and why.

Specifics:
- "how to reach": accept the office's main email and its main phone number (either one is enough).
- Office hours on a day: the regular weekly hours for that weekday; if the office is closed that
  day, the answer is "Closed" (published true).
- Shuttle: "leaving campus" means the "Leave Ramapo" column; "gets back to campus" means "Arrive on
  Campus". "Next after T" is the first departure strictly after T. The route's page is its schedule.
- Answer from the message the student wrote; if it asks something different from `ask`, mark it
  soft and say how.

Return only JSON: a list, one object per case in order:
`{"id": "s001", "published": true, "accept": ["..."], "expected": "...", "soft": false, "note": ""}`
