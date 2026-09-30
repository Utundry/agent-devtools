# Stage N4.2 — Portable State Safety

Portable non-development snapshots are intended to move between chats, agents, machines, and archival locations. They must therefore avoid silently collecting obvious credential material from the workspace.

## Default sensitive exclusions

Workspace snapshots now exclude a conservative built-in set of obvious secret-bearing paths such as `.env`, `.env.*`, common private SSH key names, credential/service-account JSON names, and private-key container extensions.

These defaults are independent from `.gitignore`: research/document work may legitimately preserve files that are not committed to Git, so preservation does not equate "ignored" with "unsafe".

## Project-specific exclusions

Projects may add preservation-only patterns without changing context or Git behavior:

```json
{
  "preserve": {
    "exclude": [
      "private/**",
      "raw/customer-data/**"
    ]
  }
}
```

Patterns are additive to the built-in sensitive defaults and must remain workspace-relative.

Handoff inherits the same protection because non-development handoff wraps the profile-aware workspace snapshot.
