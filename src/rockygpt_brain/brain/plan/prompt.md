Say what to do about the question.

`safety` lists what is wrong with the question, and is empty when nothing is:
`emergency` someone may be harmed now, `privacy` it asks for someone else's
personal information, `secret` it asks for credentials or how Rocky is built,
`harmful` answering as asked would cause harm. Judge the question, not the
subject it raises. List every one that applies, then choose a lane anyway.

Choose one lane.

CODE     the answer is a lookup in campus data. Name the capability.
RAG      the answer is written in a campus document. Give the topic.
GENERAL  the answer is general knowledge. Say which kind with `freshness`:
         `stable` if the answer is the same whenever it is asked, and
         `current` if an honest answer would have to say "as of" some date —
         anything measured, counted, priced, ranked, or currently held, however
         slowly it moves. For `current`, give the `query` to look up: what it
         means, in words. Leave the date out — Python adds it.

`capabilities` is everything Rocky can look up, and the fields each one allows.
For CODE, name one capability and use only its fields.

Narrow the rows with `filters`, drawn from that capability's filter fields.
Then say what to do with the rows that are left: `orderBy` one of its fields
with a `direction`, a `limit`, `count` to answer with how many there are,
`compare` to report fields side by side. Name only fields the capability lists.

A filter value may be one of `timeWords` in place of a date or a time. Python
resolves it against `currentTime`. Do not work out any date yourself.

The question has already been read and written out in full. There is no
conversation to consult: what you are given is all there is.
