# Stage D1 — Work Continuity Foundation

## Goal

Recover useful coding-agent work state quickly after a chat/session timeout without introducing a task server, daemon, workflow engine or remote memory dependency.

The continuity layer intentionally composes data Agent DevTools already knows:

```text
current source/Git state
+ tiny task.json
+ latest check/certification run report
+ C3 affected context
= brief/resume packet
```

The repository remains authoritative for code. The conversation is not required to reconstruct which files are dirty, what the latest verification did, or where an interrupted check stopped.

## Task state

`.agent-work/task.json` is a small local working note, not a project database. It records only the high-value continuity fields:

- goal;
- scope;
- constraints;
- definition of done;
- decisions/blockers;
- current progress summary;
- optional changed-file/verification notes;
- next step.

It is deliberately JSON rather than SQLite. No state machine or claim/lease protocol exists.

## Timeout/session recovery

`agent resume` is the primary recovery path after losing a chat session.

It reconstructs:

1. task goal/progress/next step if available;
2. current Git HEAD/branch and working-tree changes;
3. the newest Agent DevTools run report;
4. whether a report left as `running` is actually stale/interrupted;
5. the last in-progress check stage and completed check statuses;
6. impact scope and a hard-budget C3 affected-context packet.

A killed process does not need to finalize its report for recovery to remain useful: `RunWorkspace` already persisted the current stage before process execution, and D1 reads that stale report on the next session.

This is intentionally more reliable than a manually maintained conversation summary: current code and check state are re-derived from the working tree.

## Portable checkpoint

`agent checkpoint create` covers the stronger failure mode where the whole working environment may disappear.

A checkpoint ZIP contains:

```text
checkpoint.json
brief.json
task.json              # when present
files/<changed files>   # exact bytes, binary-safe
MANIFEST.sha256
```

Deleted tracked paths are recorded in metadata rather than as empty files.

When Git is available, the checkpoint also records base HEAD plus per-file base SHA-256. Restore is safe by default:

- wrong Git HEAD is rejected;
- a file may be overwritten only if it is absent, already equals the checkpoint target, or still equals the recorded base;
- a deletion is applied only when the current file is absent or still equals the recorded base;
- divergent local state requires explicit `--force`.

This allows a checkpoint to restore cleanly into a fresh checkout while protecting unrelated work in an existing checkout.

Without Git, checkpointing still works with `--include` paths or files recorded in task state.

## Non-goals

D1 does **not** implement:

- MCP/server state;
- task FSM;
- leases/heartbeats/work handles;
- automatic multi-agent coordination;
- background autosave daemon;
- remote backup;
- SQLite task state;
- LLM summarization.

If multi-agent coordination becomes a demonstrated requirement later, it should be layered on top rather than making local continuity depend on a service.

## Commands

```bash
agent task start --goal "..." --next-step "..."
agent task update --summary "..." --changed path --verified "..." --next-step "..."
agent task show
agent task clear --yes

agent brief --budget 1400
agent resume --budget 1400
agent resume --json

agent checkpoint create
agent checkpoint create --out ../handoff.agent-checkpoint.zip
agent checkpoint inspect ../handoff.agent-checkpoint.zip
agent checkpoint restore ../handoff.agent-checkpoint.zip
```

## Recovery operating pattern

For long work:

```text
start/update tiny task state
        ↓
edit normally
        ↓
checks continuously persist run reports
        ↓
session timeout
        ↓
new session runs `agent resume`
        ↓
continue from current tree + last stage + next step
```

For environment migration or an ephemeral container, create/export a checkpoint after meaningful work slices. A checkpoint is a portability aid, while `resume` is the fast normal timeout-recovery path.
