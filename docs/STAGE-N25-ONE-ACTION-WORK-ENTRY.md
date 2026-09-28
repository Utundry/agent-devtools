# Stage N2.5 — One-action Work Entry

`work enter` is the high-level entrypoint for beginning or resuming substantial work.

```bash
python devtools/agent/agent.py work enter
```

If an active task already exists, the command resumes it and emits a fresh resume brief. It does not require the agent to remember a separate `resume` command.

For a clean workspace or after a completed task:

```bash
python devtools/agent/agent.py work enter --goal "..." --no-material-gaps
```

starts the new work item, records the explicit no-gap fast path, and emits the orientation brief in one action. Without `--no-material-gaps`, alignment stays pending and the normal Task Gap Resolution Gate remains in force.

A portable handoff can be entered directly:

```bash
python devtools/agent/agent.py work enter --handoff handoff.agent-handoff.zip
```

This delegates to the manifest-verified handoff restore and returns the restored task plus a fresh resume brief.

## Safety / idempotence

- an active task with a different supplied goal fails closed unless `--replace` is explicit;
- new-task fields never silently mutate an active task;
- previously identified material gaps cannot be erased with `--no-material-gaps`;
- `--force` is only meaningful for explicit handoff restore;
- a completed task is replaced automatically only when a new explicit `--goal` is supplied.

## Principle

**Entering work should express intent, not require the agent to reconstruct lifecycle ceremony.**
