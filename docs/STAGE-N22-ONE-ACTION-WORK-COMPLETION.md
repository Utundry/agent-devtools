# Stage N2.2 — One-action Work Completion

`work complete` collapses routine completion ceremony into one semantic action.

For development profiles:

```bash
python devtools/agent/agent.py work complete
```

runs:

```text
alignment/blocker gate
→ durable knowledge validation
→ affected verification
→ fresh PASS evidence check
→ work finish
```

`--no-cache` and `--resume` are forwarded to the affected verification run.

For research, analysis, document, and general profiles Agent DevTools does not invent semantic evidence. `work complete` validates the latest explicit verification record and finishes when it is fresh and successful; otherwise it fails safely. `work finish` remains the lower-level primitive when verification was collected separately.

Principle: **Automate ceremony, not judgment.**
