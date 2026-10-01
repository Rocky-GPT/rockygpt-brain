# Historical evidence

These records describe earlier checkpoints, not the current `/v1/chat` runtime.
They are retained so failures and past measurements remain inspectable. Links and
commands inside a saved record may refer to modules that no longer exist.

`verification-20260904.md` is the unchanged earlier verification report.
`entity-facts-pre-office-slice.md` preserves the earlier, broader entity contract;
the rebuild implements only the [current office contract](../entity-facts.md).
`runners/*.py.txt` preserves obsolete runners as text: they are not executable
checks of this rebuild. The decisions and shuttle runners import missing modules;
the conversation runner assumes obsolete diagnostics and supplies authored
assistant replies instead of replaying actual service answers. The standalone
understanding/plan modules, their runners, and their tests are preserved as text
under `components/`, `runners/`, and `tests/`. They were never the full answering
path. The paid understanding runner bypassed service accounting and is retired.

Use [current verification](../verification.md) for the active checks. Historical
successes and component-classifier scores do not establish current answer quality.
