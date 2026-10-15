# Grading instructions (given to every grader as is)

You are grading two campus-chatbot replies, A and B, to the same student question. You do not know
which system wrote which; do not guess. Each case gives the question, the answer key written from
Ramapo's own pages (`published`, `accept`, `expected`) and the two replies. Judge only against the
key. Do not browse.

Label A and B:
- `correct`: published: the reply gives at least one `accept` value (formatting may differ:
  "201-684-7695" = "(201) 684-7695", "4:30pm" = "4:30 PM") and nothing that contradicts the key.
  Not published: the reply says it is not published / not available (pointing to the office or
  another way to ask is fine) and gives no value for it.
- `incomplete`: published, the reply gives none of the `accept` values and no conflicting value,
  but it does help (it names the right office and a way to ask, or gives a related true fact).
- `declined`: no `accept` value and no useful help: "I don't know", an error, an empty reply, or a
  clarifying question.
- `wrong`: the reply states a value that conflicts with the key: a different email, phone, room,
  hours or time than the key accepts, or "closed" when the key gives hours or the reverse. `wrong`
  wins over every other label. Extra details beyond what was asked are not wrong unless they
  conflict with the key.
- `unverified`: only when the key says not published: the reply gives a value for it anyway. The
  labelers' pages did not show it, so it may or may not be real; it is listed for checking, not
  counted as wrong.

Return only JSON: a list with one object per case, in order:
`{"id": "s001", "A": "correct", "B": "incomplete", "note": "<one short reason, required for every wrong>"}`
