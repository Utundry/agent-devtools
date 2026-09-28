# Stage N1 — Task Gap Resolution Gate

## Goal

Make reliable task clarification part of the harness rather than depending on the model's spontaneous discipline.

The required sequence for substantial work is:

```text
task received
  → orient / work start
  → detect material task gaps
  → propose concrete options + recommended choice
  → explicit user alignment when material gaps exist
  → task alignment ready
  → substantial execution
  → verification / knowledge / finish
```

The gate is intentionally lightweight. It is not a workflow server or a task FSM and it does not attempt to infer product requirements mechanically.

## Material-gap rule

Ask only about uncertainty whose wrong resolution would materially change the requested result, violate an important constraint, or cause expensive rework.

Do not ask the user to decide cheap, reversible implementation details merely to satisfy the gate. Those choices belong in normal assumptions/decisions.

When a material gap exists, the agent should not ask only “what do you want?”. It should present a small set of viable options, identify the recommended option and why, then obtain explicit approval or clarification before substantial implementation.

## Runtime state

New task state contains `taskAlignment` with `pending`, `clarification-required`, or `ready` status, material gaps, the proposal shown to the user, a compact resolution, and whether explicit user approval was obtained.

Old task files without this object are treated as legacy-aligned so runtime upgrades do not strand unfinished sessions.

## Commands

Record gaps and the concrete proposal shown to the user:

```bash
python devtools/agent/agent.py work align \
  --gap "Target platform changes architecture and delivery" \
  --gap "Interaction model changes controls and UX" \
  --proposal "Recommend browser delivery with keyboard controls; desktop packaging can remain optional"
```

After explicit user approval/clarification:

```bash
python devtools/agent/agent.py work align \
  --user-approved \
  --summary "User approved browser delivery and keyboard controls"
```

If the task is already sufficiently specified:

```bash
python devtools/agent/agent.py work align \
  --no-material-gaps \
  --summary "Acceptance criteria and important constraints are already explicit"
```

`work finish` is a backstop: a new work session cannot be completed while alignment remains pending or clarification-required.

## Design principle

**Autonomy starts after alignment, not instead of alignment.**

Equivalent operational rule: **ask only what is expensive to get wrong.**
