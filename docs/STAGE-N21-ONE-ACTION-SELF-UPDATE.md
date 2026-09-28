# Stage N2.1 — One-action Self Update

Updating Agent DevTools in a consumer workspace is a semantic action:

```bash
python devtools/agent/agent.py self-update
```

The command discovers the version pinned by the canonical `AGENT-START-HERE.md`, downloads that exact installer into a temporary directory, verifies it with `--self-check --expect-version`, applies it to the current workspace, then proves the installed runtime identity from both `agent_devtools.__version__` and fresh `capabilities --json`.

For read-only discovery:

```bash
python devtools/agent/agent.py self-update --check
```

For a deterministic target:

```bash
python devtools/agent/agent.py self-update --version 0.8.4
```

A same-named local bootstrap is never reused as evidence of freshness.

**The user states the intent to update; Agent DevTools owns freshness, identity, execution, and verification ceremony.**
