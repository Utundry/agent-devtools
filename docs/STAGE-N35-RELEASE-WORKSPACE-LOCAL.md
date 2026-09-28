# Stage N3.5 — Workspace-local-aware release preflight

N3.3 introduced content-addressed workspace-local marks and N3.4 completed
self-host affected classification. Release publication still used raw
`git status --porcelain`, so an otherwise valid checkout required manual stash
before every release.

N3.5 makes the release path consume the same change semantics:

- exact workspace-local marks are allowed by release clean-tree checks;
- unmarked dirty paths still fail closed;
- a marked file whose bytes changed after marking fails closed;
- generated release files are still the only non-local changes accepted during publication;
- local-only paths are omitted from release `changedFiles`;
- rollback snapshots exact workspace-local bytes before publication and restores them after `git reset --hard`.

This does not make release checks generally permissive. It only recognizes
explicit, exact, content-addressed local state already established by the
canonical change-set contract.
