# Shuttle questions, benchmark and known limits

Milestone 5, step 4 (2026-09-29). This records how the six Jev shuttle questions were tested,
what the tests showed, and what is frozen. It does not claim a pass. The design missed the bar
it was tested against by one question on the last set, and Dan chose to accept it and move on.

## What is frozen

- Six shuttle questions (`shuttle_ask.py`) that ride in the same one Jev call as the nine
  decisions: is it about a shuttle's times or stops, what does it want to know (leaves, stops,
  or a ride from somewhere else), which trip (next, first, last, or the whole day), is a clock
  time or part of the day attached, which day, which stop.
- A floor of 0.6. After every gate says yes, if Jev's least sure shuttle pick is under 0.6, the
  turn is "not ready" (`unsure`). Code otherwise follows the top pick.
- No case-specific rules. The wording is the first draft plus one sentence in the "leaves"
  option (a place named as where the trip goes or stops only narrows which trips count).
  Nothing else was reworded to fit a result, and no more wording or threshold changes are
  planned for this slice. A change to the questions needs a fresh blind set.

## How it was tested

The bar was set before any run: no wrong answers among the firm questions, and at most 3 firm
questions that should be answered but were refused. Each blind set is 60 questions written from
a plain-language contract by fresh writers who had not seen any code, wording, practice case or
earlier set, and labeled three times (the writer and two fresh labelers). Only questions all
three agreed on and both labelers were sure of are scored ("firm"). Each set was used once.

| Run | Design | Firm right | Wrong or false | Refused but valid |
|---|---|---|---|---|
| 1 (`blind`) | five questions | 52 of 57 | 0 | 5 |
| 2 (`blind2`) | six questions, "leaves or stops" restored | 55 of 59 | 0 | 4 |
| 3 (`blind3`) | plus the one sentence | 57 of 59 | 1 | 1 |
| 4 (`blind4`) | plus the 0.6 floor | 54 of 58 | 0 | 4 |

Run 4 missed the bar by one refused question. It is recorded as a miss, not a pass.

The one wrong answer (run 3) answered a return-trip question as a departure from Ramapo on a
0.50 pick. The floor was added for it. In run 4 the floor turned a wrong day (Friday for "the
day after tomorrow", at 0.49) into a refusal. It also refused one correct answer (0.55).

## Known limits

- "Stops at" wording. A shuttle question that says "the shuttle that stops at X" or "hits X"
  sometimes gets the "stops" pick, or a soft "leaves", and is refused.
- "Tonight" is sometimes read as a part of the day. In three of the four runs a question with
  "tonight" got a clock pick near 0.5 (0.44, 0.55 and 0.53), and "the last shuttle tonight" was
  refused.
- Low confidence means refuse, not guess. About 1 in 6 valid shuttle questions may get "not
  ready", which is what every shuttle question gets without this slice.
- Not built: the whole day's list, the stops a trip makes, clock times, a ride from somewhere
  other than Ramapo, a filter by route, and follow-ups. Each is refused.

## Files

- `evals/shuttle/blind*/cases.json`: the four sets, each frozen by a hash. All are spent.
- `evals/shuttle/cases.json`: 99 practice cases. Answer key corrected on 09-29 for 097 and 098
  (another route takes them) and the note on 073 (no route filter, so every route answers).
- `scripts/check_shuttle_blind.py`: the runner. It checks the hash, asks Jev once per question
  with the fixed clock and the saved timetable, keeps the spend in memory (no Neon writes) and
  stops at $0.10.
- `tests/test_shuttle_blind.py`: checks the hashes, that no question repeats across sets, and
  that a Jev that understands every label perfectly gets every row right.
