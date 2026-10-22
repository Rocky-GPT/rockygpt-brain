# Evaluation evidence

`chat/` contains current HTTP-route regression cases (an offline integration check using a
scripted provider and synthetic canonical facts) and also live evidence from October 1:
paid captures, a real-directory oracle, a readiness diagnostic and an AI-authored review
(`office-holdout-20261001-*`, `office-slice-20261001-live-smoke*`). Those live files are
dated evidence, not regression sets. It checks how the
service handles tool results and failures; it does **not** measure a real model's
understanding, real campus coverage, or readiness for production.

`conversations/`, `decisions/`, `plan/`, `shuttle/`, and `understanding/` preserve
earlier experiments. Their saved results remain unchanged. Some test components
disconnected from the current HTTP route and some used an earlier runtime. Treat
them as historical evidence, not as a release gate for the current Brain. A case
seen during development is a regression case on later runs, even if its original
filename contains `blind`.

Release evaluation must exercise complete conversations through `/v1/chat`,
feeding each actual response into the next request. Record code, configuration,
prompt, scorer, and data fingerprints with every report. Separate these outcomes:

- support: are factual claims supported by the retrieved sources?
- completion: were the supported requested parts answered?
- refusal: were unavailable account actions denied without losing public facts?
- error: was the correct failure reported with the right retry behavior?

Automatic source-membership and expected-text checks are limited regression
checks. They cannot establish semantic support for arbitrary generated prose.
Review unseen real-model conversations independently before making release claims;
report all failures and any incomplete coverage, not just one combined score.
