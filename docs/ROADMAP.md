# Roadmap

## Stage A — Agent Check extraction parity — complete

- A1: policy/impact/fingerprint/evidence foundation;
- A2: managed runner, workspace, ordinary content-addressed cache and detached status;
- A3: structured result adapters and declarative architecture presets;
- A4: file-set execution, preconditions, generated-output contracts and DRY composite presets;
- A5: certified PASS evidence, `certify --cold`, per-file reuse and engine-identity invalidation;
- A6: deterministic zero-Git exact replay, replay cold certification and generated-byte equality.

## Stage B — Release core — portable core complete

### B1 — release foundation — complete

- exact replay reused as the release reproducibility proof;
- deterministic source/files package ZIPs;
- package manifests with per-file hashes and fingerprints;
- release evidence + checksum set;
- staged self-verification and atomic publish;
- `agent release build` / `agent release verify`.

### B2 — self-release qualification — complete

- Agent DevTools releases itself from the previous certified source snapshot through the public facade;
- the produced source package is accepted as the next base;
- no repository-specific engine branch is used.

### B3 — composable release presets — complete

- reusable source/PHP/Vite release components;
- matching package rows merge by stable `id`;
- `php-vue-vite` composes one runtime package from independent backend/frontend fragments;
- project-name-agnostic presets through artifact-prefix fallback.

Optional Git-patch export, signing and deployment-provider hooks remain future interoperability layers, not prerequisites for Stage C.

## Stage C — Agent Context

### C1 — local context index — complete

- repository text discovery with project ignore/include rules;
- disposable SQLite index and atomic rebuild;
- incremental SHA-256 reindexing;
- FTS5 with lexical fallback;
- stable explicit semantic anchors plus cheap natural/fallback chunks;
- provenance-rich bounded query and anchor inspection;
- Agent DevTools self-host retrieval.

### C2 — affected context — complete

- reuses Check change discovery and impact graph;
- preserves safety/fallback-full selection semantics;
- derives a compact retrieval query from impact + changed paths;
- returns bounded provenance-rich affected briefings.

### C3 — relevance and semantic addressing — complete

- stable natural-symbol identities no longer depend on line positions;
- JSON/TOML/INI structural selectors and optional `agent-context.map.json` sidecar;
- semantic metadata validation;
- hard excludes plus low-priority source penalties;
- impact-first filtering with one-hop relation preference;
- file-first candidate narrowing, overlap/Jaccard deduplication and source-kind caps;
- hard approximate-token budget with explainable omission counters;
- context policy supplied by reusable preset composition.

### Next C slices

- C4 only if dogfooding proves a need for stronger relation discovery, aliases or history weighting;
- keep lexical retrieval and zero-service operation as the mandatory baseline.

## Stage D — Work continuity and consumer dogfooding

### D1 — lightweight work continuity — complete

- tiny JSON task state for goal/scope/constraints/definition-of-done/progress/next step;
- `agent brief` / `agent resume` assemble current work from task state + Git + latest check evidence + bounded affected context;
- stale `running` reports from killed/timed-out sessions are detected as interrupted work and expose the last stage;
- portable checkpoint ZIP captures unfinished file bytes, deletions, task state and manifest;
- safe restore checks Git base and per-file base/target hashes before overwriting;
- no server/FSM/lease/daemon/SQLite dependency for work continuity.

### D2 — semantic diff — only after D1 dogfooding

Map changed hunks to stable symbols/anchors so resume/context/check can reason about changed semantic objects rather than whole files. Do not add a heavy language server or call-graph framework.

### Consumer dogfooding

Vendor the same portable toolkit into the original production consumer with only project configuration/adapter differences. Prove that cache deletion and new-session recovery are routine.

## Stage E — second-project portability proof

Use the unchanged portable code in a materially different repository. Agent DevTools 0.1/0.2 lineage is not considered fully portable until at least two consumers use the same core.
