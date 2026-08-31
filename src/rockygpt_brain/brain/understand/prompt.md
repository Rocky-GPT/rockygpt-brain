Read the question on its own. There is no conversation in front of you and none
is coming, so nothing here may assume one.

`normalized`            the question with its wording tidied — spelling,
                        spacing, punctuation — and nothing else. Follow
                        nothing, fill in nothing.
`unresolvedReferences`  every span of the question that cannot be understood
                        from the question alone. Some are a word standing in
                        for a thing — "it", "that one", "there". Some are a
                        subject left out altogether: "what about tomorrow"
                        names a day and no topic, so the topic is missing, and
                        the words carrying the gap are the span. A question
                        about what was said earlier points wholly at it, so
                        give the span that does the pointing. Nothing already
                        understood belongs here — not "you", not "Rocky", not a
                        date the clock gives.
`needsContext`          true when `unresolvedReferences` has spans in it and
                        false when it is empty. The two are one fact stated
                        twice and must agree.

A question that stands on its own has no spans and needs nothing. That is the
ordinary case, and it is worth saying plainly: a gap reported where there is
none sends a whole question to be rewritten against a conversation it never
mentioned, and it comes back meaning something else.
