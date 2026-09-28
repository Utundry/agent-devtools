# Stage N3.3 — Workspace-local change classification

Self-host dogfood showed that local workspace infrastructure can enter the
canonical change-set and force fail-safe full verification.

N3.3 adds content-addressed workspace-local marks:

    python agent.py changes local mark .gitignore AGENTS.md --reason "self-host infrastructure"
    python agent.py changes local list
    python agent.py changes local clear .gitignore

Marks live under `.git/agent-devtools-local-changes.json`, outside project
source. A path is suppressed only while its current bytes exactly match the
recorded SHA-256. Any later edit automatically makes the path canonical again.

Workspace-local entries stay visible in diagnostics. Patch construction also
honors the canonical tracked set, so an exact local-only tracked file cannot
leak into a project patch.
