# Stage N3.1 — Dogfood Hardening

This stage turns the first real 0.8.8 dogfood findings into narrow hardening changes instead of adding another subsystem.

## Stale run-state recovery

External shell/agent timeouts may terminate a check runner without giving it a chance to remove `.agent-work/run.lock` and `active.json`.

The runtime now reaps dead stale state automatically before reporting/starting runs. On Linux, zombie PIDs are treated as dead instead of being accepted merely because `kill(pid, 0)` succeeds. Live run state is never reaped.

## Advertised workflow must match the CLI

`workflow show` and capabilities advertised `knowledge status`, but the command did not exist. `knowledge status` is now a real read-only command that summarizes record counts/effective statuses and includes conflict/dangling validation.

## Routine bugfix fast path

High-level `work enter --goal ...` now records the no-material-gaps fast path automatically for a newly created task.

For genuinely ambiguous work:

```bash
python devtools/agent/agent.py work enter --goal "..." --alignment-pending
```

`work start` remains the lower-level primitive whose alignment starts pending. Existing active tasks and restored handoffs preserve their current alignment unless explicitly changed.

The common reversible bugfix path becomes:

```text
work enter → fix → work complete
```

**Hardening should remove ceremony, not add another subsystem.**
