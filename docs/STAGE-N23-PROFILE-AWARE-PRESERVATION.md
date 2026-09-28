# Stage N2.3 — Profile-aware Preservation

The agent should express the intent to preserve recoverable project state without remembering whether the active profile uses a development checkpoint or a non-development workspace snapshot.

## Create

```bash
python devtools/agent/agent.py preserve create
```

Routing is deterministic:

```text
development
→ unfinished-work checkpoint

research / analysis / document / general
→ portable workspace snapshot
```

Development-specific explicit file inclusion remains available with `--include`. Non-development preservation captures the workspace corpus automatically, so `--include` fails explicitly instead of being silently ignored.

## Inspect and restore

```bash
python devtools/agent/agent.py preserve inspect artifact.zip
python devtools/agent/agent.py preserve restore artifact.zip
```

The archive kind is determined from its semantic metadata entry (`checkpoint.json` or `snapshot.json`), not from its filename or extension.

Existing lower-level `checkpoint` and `workspace-snapshot` commands remain available.

**The user states the preservation intent; Agent DevTools chooses the profile-appropriate recovery primitive.**
