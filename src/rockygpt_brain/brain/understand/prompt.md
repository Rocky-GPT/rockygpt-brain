# BRAIN #1 — understand

The highest-churn, highest-risk text in the brain. A sentence added here has
twice moved lane routing on questions it was not about.

It describes steps, not questions. No phrase, entity, or example from any test
belongs here — prose added to fix one question reliably breaks three others.

---

Work out what the question is asking, in four steps, in this order.

`normalized`  the question with its wording tidied — spelling, spacing,
              punctuation — and nothing else. Follow nothing, fill in nothing.
`references`  everything the question borrows from `earlierTurns`, each with
              what it stands for. Some are a word standing in for a thing —
              "it", "that one", "there". Some are a subject left out
              altogether: "what about tomorrow" names a day and no topic, so
              the topic is borrowed; put the words that carry the gap in
              `text`. Nothing that needs no conversation belongs here — not
              "you", not "Rocky", not a date the clock already gives.
`usedTurns`   the positions in `earlierTurns` those references point into,
              counting from 0. Empty when there are no references.
`usesContext` true when this question needs the conversation — because it
              borrows from it, or because it is about what was said. False for
              a question that would mean the same asked first.
`resolved`    the question rewritten to stand on its own, with what it pointed
              at written in. It is still a question and still the one asked —
              do not answer it, and do not replace it with what was said
              before. A question that points nowhere resolves to `normalized`.

Whoever reads `resolved` next will not see this conversation, or the words as
typed. Everything the question needs has to be in it.
