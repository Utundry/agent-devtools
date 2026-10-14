# Task and UI Alignment · incremental workflow (0.16.45)

Agent DevTools is not a reasoning engine. The agent interprets user intent, identifies
material expectation gaps, proposes choices and requests user approval where needed.
The tool records the resulting evidence in the existing `taskAlignment` state.

For tasks affecting UI, the agent should discuss the user journey, expected entry point,
principal interaction and UX risks **before substantial implementation** where a choice
could materially affect expectations. Use the existing `work align` command:

```
agent work align --gap "Where should the user open the act?" \
  --proposal "From execution/acceptance; include several positions" \
  --interpretation "Prepare one printable act for selected accepted positions" \
  --ui-scenario "Select accepted positions, preview, print" \
  --ui-entry-point "Execution and acceptance section" \
  --ui-interaction "Prepare act, preview, print without leaving context" \
  --ui-risks "Do not force one act per position"
agent work align --user-approved --summary "Agreed combined act in acceptance"
```

The `--interpretation` and `--ui-*` parameters are optional. Routine tasks continue
through `agent work enter` without new required gates or questions. Where a material
gap is already recorded, the fast path cannot silently bypass user approval.
If a new material gap is discovered mid-task, use `work align --gap` to reopen it.
A completed task cannot be reopened using alignment; start a new work item.
