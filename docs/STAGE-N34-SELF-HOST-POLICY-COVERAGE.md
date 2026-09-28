# Stage N3.4 — Complete self-host affected classification

N3.3 removed exact workspace-local infrastructure from the canonical change-set.
Dogfood then exposed the next independent problem: several real top-level
`agent_devtools/*.py` modules had no source rule, so they were classified as
`unknown` and forced fail-safe full verification.

N3.4 closes that policy coverage gap without weakening fail-safe behavior.

The existing intentionally global entry points remain global:

- `agent_devtools/cli.py`
- `agent_devtools/project.py`
- `agent-tools.json`
- `agent-check.policy.json`

Other top-level Agent DevTools orchestration/runtime modules are explicitly
classified as `core`. This is conservative: `core` propagates to check, release,
context, and tests, but it does not trigger the reserved `global` or `unknown`
fallback semantics.

A regression test enumerates every top-level Python module under
`agent_devtools/` and fails if any future module is left unclassified.
