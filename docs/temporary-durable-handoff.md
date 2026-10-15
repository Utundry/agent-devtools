# Temporary Durable Handoff: initial task exchange slice (0.16.47)

This extends existing `agent handoff create/inspect/resume` ZIP preservation without changing it.
Task JSON files live in `.agent-handoffs/` and are excluded locally via `.git/info/exclude` when Git is present. For shared repositories also add `/.agent-handoffs/` to project `.gitignore`.

Example with two independent workspaces:

```sh
agent handoff tasks new --kind delegation --goal 'Check editor UX' --step 'Open editor' --step 'Insert table column'
agent handoff tasks list
agent handoff tasks export-task ID --out assignment.json
# copy assignment.json to local tester workspace
agent handoff tasks import-task assignment.json
agent handoff tasks list           # discover only, NEVER auto claim
agent handoff tasks claim ID --actor local-test-bot  # save claimId from output
agent handoff tasks result ID --claim-id TOKEN --outcome pass --summary 'Verified' --receipt-out receipt.json
# copy receipt.json to cloud workspace (or paste its complete JSON through user)
agent handoff tasks receive receipt.json
agent handoff tasks close ID --receipt-sha RECEIPT_SHA --yes
# on tester workspace: agent handoff tasks close ID --claim-id TOKEN --yes
```

Policy defaults are in `agent-tools.json` under `handoff` with keys documented by the defaults in `handoff_tasks.py`. TTL is disabled by default; zombie warning is 30 idle days. `cleanup` removes only expired **unclaimed** handoffs. A claimed task is never removed by automated cleanup. Files are not synchronized; a receipt is portable, checksum-checked against the assignment and explicitly ingested.

Limits of this slice: only local shared-filesystem exclusive claims (atomic O_EXCL); separate imported copies can each be claimed unless externally coordinated. No background service, global leases, authentication, or secure cross-machine identity. SHA-256 detects accidental alteration, not forgery; imported tasks and receipts are untrusted and must be inspected before acting. A direct transport beyond manual file delivery is not yet implemented. `result-ready` is not a user acceptance verdict, and a FAIL outcome can be a correctly completed test mission.
