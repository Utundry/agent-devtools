# Stage N1.1 — Frictionless Alignment Fast Path

## Goal

Keep the Task Gap Resolution Gate explicit in project state without turning it into a mandatory user-visible ceremony.

The operational rule is:

```text
task received
  → detect material gaps
  → no material gaps: mark ready and continue immediately
  → material gaps: propose concrete options and obtain explicit user alignment
```

Alignment is always represented in task state. A user interaction is required only when a material ambiguity is expensive to get wrong.

## Fast path

For a sufficiently specified task, the agent should use:

```bash
python devtools/agent/agent.py work align --no-material-gaps
```

No `--summary` is required. The runtime records a stable default rationale so the state remains explicit and resumable.

Typical fast-path tasks include:

- a concrete bug with an observable expected result;
- applying an exact supplied patch;
- a narrowly scoped mechanical refactor with explicit acceptance criteria;
- a reversible local change whose unresolved details do not materially affect the result.

The agent must not ask the user a question merely to satisfy the gate.

## Clarification path

The existing N1 rule remains unchanged for material uncertainty:

- record the material gap;
- provide concrete viable options and a recommended option;
- wait for explicit user approval or clarification before substantial execution.

Cheap reversible uncertainty belongs in assumptions/decisions rather than user interrogation.

## Durable knowledge threshold

Durable knowledge promotion is selective, not ceremonial.

If a task produces no reusable project-level knowledge, promoting zero records into `.agent-knowledge/` is a correct completion outcome. One-off bug details, transient hypotheses and obvious implementation facts should remain session-local.

## Principles

**Alignment should be explicit in state, not necessarily visible as friction to the user.**

**Never ask a question merely to satisfy the gate.**

**Promote knowledge because it will matter later, not because the workflow has a knowledge phase.**
