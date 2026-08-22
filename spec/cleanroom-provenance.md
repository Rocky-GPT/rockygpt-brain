# Clean-room provenance

The specification was distilled on 2026-08-22 from consumer-facing interfaces
and black-box operational requirements only. No file under the existing
`rockygpt-brain` repository was opened, searched, copied, or used as a source
for this workspace.

Allowed source snapshots:

| External boundary | Commit |
| --- | --- |
| UI consumer | `a63cd34e9e20809b1c20ec5f2ea3554a0b581a8b` |
| Campus data service | `f1637beaad1adb125af36ecbfed215cc5b7fa266` |
| Black-box evaluations | `d02c47286101a0d21b54886881f9c166db43c91e` |
| Deployment and smoke tests | `432afd0f305855ae368c8b1d3ec35a750e799937` |

The source repositories and their Git history are deliberately unavailable to
the implementation agent. This repository contains the full handoff.

`spec/data-api.openapi.yaml` is a byte-for-byte snapshot of the data service's
public OpenAPI contract at the recorded commit. Its SHA-256 digest is
`9fc1de7ffea2a78f7082fb8259b1ff04993153c3222c987208ef4f9b51414f5a`.

The specification fixes external behavior only. It intentionally excludes all
prior prompts, internal modules, tools, control flow, model-call strategy,
database design, framework choices, and test implementation details.
