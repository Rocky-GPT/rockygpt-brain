# Grading instructions (given to every grader as is)

You are grading two campus-assistant answers to the same question. You see the question, the
official answer Ramapo College publishes for it on its own page (with the page URL), and two
answers labeled A and B. You do not know which system wrote which; do not guess.

Judge only against the official answer and plain common sense. Do not browse.

For the question, first decide `scorable`:
- false when the heading is not a real question a person would ask an assistant (marketing or
  rhetorical: "Ready to take the next step?", "Why should I give?", a project title), or when the
  official text under it does not actually answer it. Grade A and B anyway.
- otherwise true.

Then give A and B one label each:
- `answered`: gives the substance of the official answer (its key facts or steps), nothing that
  contradicts it. Extra true detail is fine.
- `partial`: gives some of the official answer's substance but misses a key part, nothing
  contradicting it.
- `referral`: none of the substance, but points the person to a relevant office, contact or page
  that would handle the question.
- `declined`: none of the substance and no useful referral: says it does not know, that it is not
  published, asks a clarifying question, or is an error or empty.
- `wrong`: states something that contradicts the official answer, or a fact that is plainly false
  or made up. `wrong` wins over every other label, even when the rest is good. Saying something is
  "not published" or "unknown" when the official page has it is `declined`, not `wrong`.

Return only JSON: a list with one object per case, in order:
`{"id": "q001", "scorable": true, "A": "answered", "B": "referral", "note": "<one short reason, required for every wrong and for any scorable=false>"}`
