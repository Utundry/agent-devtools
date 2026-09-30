# Stage N4.4 — Git Worktree Marks

Workspace-local marks now use Git-native `git rev-parse --git-path` resolution instead of assuming `.git` is a directory.

Regular repositories preserve the old `.git/agent-devtools-local-changes.json` location; linked worktrees maintain independent mark files in Git's worktree-specific administrative directory. Exact SHA-256 matching still governs exclusion from canonical patches, and a mismatch re-enters the canonical change set.

No changes to mark schema, release preflight or canonical patch semantics.
