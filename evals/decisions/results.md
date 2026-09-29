# Milestone 4 live results

`scripts/check_decisions.py` asks Jev the 130 labeled questions in `cases.json`, one
call each, through the development ledger on Dan's Mac. An item is **right** when Jev
was sure (0.90 or more) and the label accepts its answer, and **unsure** when it
wasn't sure, so the Brain decides nothing from it. **Top pick** counts Jev's first
choice whether or not it was sure.

## Run 1: 2026-09-29, commit 06a6f76

130 questions, 0 skipped, $0.0155 in total, Jev median 181 ms (max 360 ms).

| Item | Right | Unsure | Wrong | Top pick |
| --- | --- | --- | --- | --- |
| What it asks | 92/130 | 38 | 0 | 98% |
| Needs history | 67/115 | 48 | 0 | |
| Dangerous (Jev) | 130/130 | 0 | 0 | |
| Private, live-only or unsupported | 68/130 | 60 | 2 | 92% |
| Campus area | 88/130 | 42 | 0 | 95% |
| Kind of thing named | 66/130 | 61 | 3 | 82% |
| Which handler | 99/130 | 0 | 31 | |
| Several separate asks | 113/128 | 15 | 0 | |

Jev's first choice was nearly always right, but it was often not sure enough to act
on. Every wrong handler was GPT picked because something was unsure; none was a
confident wrong pick. Five readings were sure and wrong:

- "Someone passed out": the reach reading said outside knowledge (0.92), and the named
  reading said a person (0.90).
- "The last day to withdraw from a class": the named reading said a course (0.96).
- "What does G-414 mean": the named reading said a course (0.96). G-414 is a room.
- "Smoke coming from a trash can": the reach reading said right now (0.94).

What changed for run 2:

- **Options that lead to the same thing count together.** For example, "What room is it in?"
  split between campus information (0.54) and the conversation, and both are
  answerable. The reach reading, the outcome (answer, account limit or can't answer)
  and the handler now add up those options.
- **Needs history, reworded.** It sat at 0.11-0.31 for questions that stand alone and
  0.57-0.88 for ones that lean back. It now asks whether the request leaves out what
  it is about, with examples.
- **Kind of thing named** now asks for a *particular* thing, and "none" covers general
  words like "a class" or "someone".
- **What answering needs:** campus information now includes emergency guidance.
  "Right now" is a live look RockyGPT would need, not what the student reports.
  "Outside" is what has nothing to do with Ramapo or campus life.
- **Labels that were too strict:**
  - The Registrar questions accept academics as their campus area as well as places.
  - "hey rocky" accepts a person as its named thing.
  - "What day is it" accepts a date.

## Run 2: 2026-09-29, commit 1c89a3f (as merged)

130 questions, 0 skipped, $0.0160, Jev median 182 ms (max 811 ms).

| Item | Right | Unsure | Wrong | Top pick |
| --- | --- | --- | --- | --- |
| What it asks | 95/130 | 35 | 0 | 98% |
| Needs history | 73/115 | 41 | 1 | |
| Dangerous (Jev) | 130/130 | 0 | 0 | |
| Private, live-only or unsupported | 100/130 | 30 | 0 | 93% |
| Campus area | 90/130 | 40 | 0 | 97% |
| Kind of thing named | 55/130 | 74 | 1 | 93% |
| Which handler | 116/130 | 0 | 14 | |
| Several separate asks | 111/128 | 17 | 0 | |

Dan asked to stop tuning here (09-29), so this is milestone 4 as merged. Rewording
questions to raise scores on these same 130 questions would only fit them.

Known gaps, for the milestones that use each reading:

- **Which handler:** all 14 misses were GPT picked because a reading was unsure. None was
  a confident wrong pick. Examples:
  - "actually drop all my classes" went to GPT, not the account limit.
  - "is chick fil a open right now" and "what tech clubs are active right now" were split
    toward a live look.
  - "where is b-wing" leaned toward outside knowledge.
  - "go back, where was the registrar again" was split between a fact and recall.
  - Opinion questions ("which professor is the easiest") leaned toward campus information.
- **Needs history:** it is the softest reading. After a long conversation, questions that
  stand alone sat at 0.11-0.26, where 0.10 or less counts as sure. The labeled
  conversations hold only the student's questions, not RockyGPT's answers.
  Conversation State (milestone 7) should measure it on real conversations.
- **Kind of thing named:** the top pick is right 93% of the time, but it is sure only 55
  times. "G-414" read as a course. Naming the exact place or office needs the campus
  name list (milestone 5).
- **Several separate asks:** 17 were unsure. Multi-Part (milestone 10) owns it.

## Run 4: 2026-09-29, commit 1894ff7 (Jev picks every handler)

Dan then changed the design. Jev now decides where every question goes, and code
follows Jev's top pick even when it is under 0.90. GPT is a place Jev sends a question on
purpose (the new `open_ended` question, or advice and chat), not where it lands when
Jev is unsure. So from this run on, an item is **right** when the pick code followed is
one the label accepts. **Under 0.90** counts picks Jev was less sure of. For the handler,
that means any pick on the way to it. **Sure wrong** counts wrong picks Jev put at 0.90
or more. The labels are unchanged. Run 3 measured a round of wording changes that Dan
stopped, and it isn't used.

130 questions, 0 skipped, $0.0169, Jev median 177 ms (max 436 ms). Measured once.

| Item | Right | Wrong | Sure wrong | Under 0.90 |
| --- | --- | --- | --- | --- |
| What it asks | 128/130 | 2 | 0 | 37 |
| Needs history | 109/115 | 6 | 1 | 43 |
| Dangerous (Jev) | 130/130 | 0 | 0 | 12 |
| Dangerous (Jev or phrases) | 130/130 | 0 | 0 | 10 |
| Private, live-only or unsupported | 122/130 | 8 | 0 | 29 |
| Campus area | 125/130 | 5 | 0 | 39 |
| Kind of thing named | 120/130 | 10 | 1 | 70 |
| Which handler | 122/130 | 8 | 0 | 64 |
| Several separate asks | 128/128 | 0 | 0 | 15 |

GPT took 18 questions. Most were greetings ("hey rocky", "huh", "nvm"), comparisons
("which one is cheapest") and explanations ("then why are you giving me a sunday
shuttle").

Known gaps, left for later milestones rather than tuned:

- **Which handler:** 8 were wrong, and Jev was under 0.90 on all 8.
  - "What day is it today?" and "wait what day is it today" went to can't answer,
    because answering was read as a live look (0.50, 0.61). Code knows the date, so the
    label is exact.
  - "which professor is the easiest for computer science" and "which professor gives
    the most A's" went to GPT, because answering was read as campus information. The
    label is can't answer (a guess or an opinion).
  - "is my professor going to cancel class tomorrow" went to exact. The label is can't
    answer (a guess).
  - "i locked myself out of my dorm at 2am what do i do" and "what happens if i miss
    that deadline" went to GPT through `open_ended`. The label is document or policy.
  - "write me a text saying i can't make it to class" went to the account limit (0.51).
    The label is GPT or can't answer.
- **Low confidence:** 64 of 130 handler picks had at least one reading under 0.90 on
  the way. Each one is listed in `jev.decided.lowConfidence` in the export.
- **Needs history:** 6 were wrong, and one was sure ("is there an elevator nearby" read
  as standing alone, 0.93). Conversation State (milestone 7) should measure it on real
  conversations with RockyGPT's answers in them.
- **Kind of thing named:** 10 were wrong, and one was sure ("G-414" read as a course,
  0.90). The campus name list (milestone 5) should settle names.
