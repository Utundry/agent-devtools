# Changelog

## Unreleased

- R7 introduces the Declarative Update Executor: `scripts/update_release.py --scenario <json>` applies a strict stdlib-only scenario in the existing isolated preparation clone, then reuses the canonical release builder and publication/synchronization path.
- Scenario format v1 supports exact `baseBlobs` plus idempotent `replace`, guarded `write`, hash-guarded `delete`, and `assert_contains`; arbitrary shell/Python hooks are intentionally forbidden. Scenario SHA-256 is committed as provenance so retries and already-published verification are deterministic.
- This replaces generated one-off publisher/recovery scripts as the intended future release-update mechanism while preserving `--patch` compatibility.
- R7.1 isolates all update execution state under Git-ignored `.agent-updates/`; updater runs move from `build/update-*` to `.agent-updates/runs/`, applied scenarios are snapshotted per run, and scenarios remain disposable technical inputs rather than tracked project knowledge.
- R7.2 adds `--auto` for the maximal unambiguous reachable scenario chain in `.agent-updates/incoming/` and foreground `--periodic SECONDS` for interval polling. Successful scenarios move to `.agent-updates/applied/`; forks/cycles fail closed, and unchanged periodic failures are suppressed until scenario/catalog content or `VERSION` changes unless `--retry-failed` is explicit. No daemon, service, hook language, or external dependency is introduced.

- Stage R6.5 removes redundant durable-memory retrieval from the normal agent path: `begin` is explicitly the canonical first-pass semantic retrieval, and generic `rg`/`grep`/`find` rescans of `.agent-knowledge` are fallback-only when the projection is insufficient for a specific reason or an exact file/line is required.
- Capabilities expose `canonicalFirstPassRetrieval`, `manualDurableSearchFallbackOnly`, and `avoidRedundantDurableRescan`. No retrieval scoring, storage, service, dependency, or routine command changes; CLI contract advances to v32 while workflow contract remains v21.

- Stage R6.4 adds bounded cross-task recall without weakening R6.2 isolation: durable records below the primary selection gate may surface as at most three `possiblyRelated` cues when deterministic source-task-goal/domain overlap exists. These cues use a separate mini-budget and never consume the primary C3 token budget.
- Routine cognition human output is now compact: the newly recorded semantic item plus task-state counts replaces full accumulated-history reprinting; `cognition status` and `--json` retain complete inspection paths.
- Research verification help now states the compact/granular mutual exclusion directly, and capabilities/workflow document semantic exit-code classes (`0` clean success, documented `1` actionable gate/review state, `2` invalid/contract failure). CLI contract advances to v31; workflow contract remains v21 and no new routine command is introduced.

- Stage R6.3 strengthens research attestation integrity: strong contextual warnings (`missing-source-provenance`, `potentially-material-open-questions`, and `possible-stale-cognition`) now disable compact `verify research --confirm-all-pass`; the existing granular five-dimension attestation remains available so an agent can explicitly retain a dimension as pass/warn/fail.
- Task-local subject-bearing assumptions/questions now expose a deterministic possible-stale signal when later cognition with different text reuses the same subject or a hierarchical refinement such as `foo -> foo-16gb`; checkpoint and research verification surface the signal without auto-deleting, resolving, or superseding cognition.
- Same-command `--evidence` participates in source-awareness so compact PASS is not rejected merely because evidence is being supplied with the attestation. CLI contract advances to v30; workflow contract remains v21 and the routine command surface is unchanged.

- Stage R6.2 hardens cognitive quality from fresh-model hardware-research dogfood: C3 no longer admits cross-task durable knowledge on weak lexical overlap alone; term-only reuse from another task now requires multiple query-term matches plus support from the source task goal, while explicit scope/path/same-task signals remain authoritative.
- Research verification now warns when the current task has no recorded source/evidence provenance before `sourcing=pass`, and separately flags subject-bearing open questions as potentially material so the agent reviews whether they could change the recommendation or architecture.
- Research `source add` now binds source provenance to the active task when one exists. CLI contract advances to v29; workflow contract remains v21 and no new routine command, storage layer, service or dependency is introduced.

- Stage R6.1 hardens agent UX from fresh-model dogfood: `verify research` now surfaces active assumptions and open questions as contextual warnings before/after attestation while keeping deliberately retained non-blocking uncertainty legal.
- A natural invalid guess such as `cognition add --stdin` now receives one actionable semantic-type hint before argparse; `cognition add` remains invalid and is not advertised in parser inventory/capabilities.
- CLI contract advances to v28; the workflow contract and canonical R5/R6 lifecycle remain unchanged.

- Stage R6 adds UTF-8 bulk semantic capture through `cognition ... --stdin` and `--from-file`, with an exactly-one-source contract shared with positional text and `--text`; multiline/Unicode research notes no longer need shell-escaping or temporary Python wrappers.
- Adds `work report`, a read-only consolidated projection of task outcome, DoD, cognition, latest verification, knowledge health and semantic-journal status. Report generation does not mutate task state and remains optional outside the canonical lifecycle.
- Workflow/capabilities advertise bulk capture and the optional final report without expanding the R5 normal surface.

- Stage R5 introduces a canonical agent surface: routine work is explicitly limited to `begin`, `cognition`, profile-appropriate `verify`, `cognition checkpoint`, and `work complete`; lower-level task/work/knowledge/recovery commands are marked advanced rather than peer alternatives.
- `begin` advertises the normal surface in human and JSON output and accepts a matching `--profile` compatibility hint. `task update` accepts obvious aliases (`--add-finding`, `--add-assumption`, `--add-decision`, `--done`) while remaining advanced.
- Workflow/capabilities expose normal-vs-advanced command classification so weaker agents can avoid CLI exploration and guessed primitive syntax.

- R4.1 adds interruption-safe resume for transport/chat/SSE failures: the next usable turn runs `begin` without `--goal`, restoring the existing active task from project state instead of reconstructing progress from conversation memory.
- `begin` format v2 exposes recovery mode and project-state authority; workflow/capabilities advertise interruption resume without adding storage or transport hooks.

- Stage R4 adds bounded external-tool failure recovery without adding a supervisor: `cognition tool-failure` records a stable failure fingerprint, observed attempts, optional/mandatory importance and fallback guidance using existing journal/task primitives.
- The retry ceiling is two equivalent failures for the same tool/action. Optional work then falls back or skips without blocking completion; mandatory work becomes an ordinary blocker until recovered, preserving all prior cognition/progress.
- Human `workflow show` now renders the full canonical routine (`begin -> work -> checkpoint -> complete`) and advertises the failure-recovery rule directly.

- Stage R3 adds frictionless semantic capture: cognition commands accept both canonical positional text and the natural `--text` alias, with ambiguity rejected rather than guessed.
- Semantic checkpoint now classifies and reports `required`, `advisory`, and `session-only` cognition. A clean local-only checkpoint explicitly says that no durable promotion is needed.
- Routine memory guidance now makes checkpoint the authority: agents are told not to call `knowledge promote` directly before classification; manual exact promotion remains an expert primitive.
- Research onboarding/workflow now names `verify research` as the canonical verification route, reducing fallback to generic `verify record` syntax guessing.

- Stage R2 formalizes the **Agent Work Lifecycle** terminology and makes `begin` the canonical routine entrypoint across onboarding, workflow and agent handoff; `work enter` remains an explicit lower-level primitive.
- Semantic checkpoint and work-completion failures now emit executable canonical next actions instead of leaving agents to guess `knowledge promote`, `work show`, or other primitive syntax.
- `verify research` with no statuses is now a read-only five-dimension review tray. `--confirm-all-pass` provides an explicit compact attestation only after review; granular pass/warn/fail statuses remain available whenever any dimension is uncertain.
- Begin output distinguishes an empty durable project memory from an unavailable context subsystem, retains the R1 compatibility `ritual` field, and adds explicit Agent Work Lifecycle/canonical-routine metadata.

- Stage R1 establishes the normal agent work ritual `begin -> work -> checkpoint -> complete`. New `agent begin` is a thin facade over `work enter` plus durable context projection and knowledge-health reporting; it introduces no new state model.
- `cognition checkpoint --promote-required` explicitly promotes only REQUIRED subject-bearing decisions/requirements through the existing `knowledge remember` path, while ordinary checkpoint remains read-only and advisory candidates remain manual.
- Interactive shell adds `begin <goal>` and `checkpoint promote` shortcuts over the same top-level dispatcher. Workflow/capabilities advertise the ritual without hiding the underlying primitives.
- The work-ritual regression suite includes fresh-clone recovery proving that Git-tracked `.agent-knowledge` is immediately reusable by `begin` after disposable task state is absent.

- Bootstrap explicit profile selection now has precedence over inferred intent specialization; explicit neutral profiles work without a redundant intent prompt and can deliberately re-specialize an existing neutral workspace.
- Context projection CLI stage validation now derives accepted spellings from the same alias table as projection normalization, so workflow terms such as `orient`, `work`, `verify`, `checkpoint`, and `finish` are accepted consistently.
- Context Lifecycle C4 adds `agent shell` as a thin interactive UX over the ordinary top-level CLI dispatcher, with deterministic shortcut normalization, repeatable `--command` batch mode, and no shell-only project state.
- Context Lifecycle C3 adds deterministic budgeted context projection over Git-tracked durable knowledge: stage-aware ranking, hierarchical scopes, active-status filtering, near-duplicate suppression, compact cues with expand refs, selection explanations, current-task/operational projections, and disposable usage tracking.
- Context Lifecycle C2 adds Git-canonical durable knowledge lifecycle in `.agent-knowledge`: active/superseded/historical/rejected lifecycle, scope/confidence/evidence/provenance, append-friendly supersession, `knowledge remember/why/lifecycle/supersede`, and no durable SQLite dependency.
- Context Lifecycle C1 adds an append-only SQLite semantic journal behind existing cognition, a non-mutating `cognition checkpoint`, verification journaling, and semantic closeout that requires durable representation only for explicitly subject-bearing decisions/requirements while keeping local cognition lightweight.

- Release version edits and rollback invalidate only the version module's bytecode, preventing stale version imports during same-length bumps within a timestamp second.
- Routine completion automatically reuses a current, integrity-checked PASS; otherwise it runs affected checks, includes task-recorded changed files, and establishes a full baseline when changes are unknown or absent. Explicit `--no-cache` still requests execution. Default `workflow show` is compact, with detailed responsibilities under `--details` (workflow v10 / CLI v16).
- Fail-safe full selection stays full even with catch-all policy rules, and completion revalidates the actual fallback coverage before reusing a report.
- Clean-room self-hosting removes bootstrap's eager index creation, makes its launcher editable source, and includes script/bootstrap dependencies in the project's own check identities.
- Preserve historical 0.7.0.1 validation/evidence under `docs/archive/` and stop tracking its obsolete root package manifest, so future Git snapshots are not rejected as tampered release packages.
- Audit hardening: machine completion validates task/config/engine identity, report SHA-256, current input/output hashes and affected-suite/group coverage. Manual attestations are explicitly distinguished from machine checks.
- Cache and certification identity now hashes inherited environment by default; optional per-command `cacheEnv` names/patterns narrow that contract without storing environment values. Hard deadlines participate in execution identity. Reused results carry the originating report reference and integrity status.
- Shared, pruned file discovery fixes zero-directory `**/` matching across selection, cache, context, output contracts, replay and release. Executed stages invalidate the in-memory inventory; no persistent content database is required for checks.
- Snapshot/handoff payloads are streamed, size limits are checked before reading large files, repeated snapshots exclude their own output, restore preflights task/verification conflicts, and canonical archive aliases/file-directory collisions are rejected before extraction.
- Research briefings refresh an existing index; briefing metadata fingerprints are named explicitly. Fast process completion avoids the former 100 ms polling floor, and business/research intents no longer imply a software stack from generic words.
- Small-project overhead: briefings no longer create context indexes implicitly; context scanning retains safety exclusions, excludes the installed runtime and prunes ignored directories. `context.includeRuntime` supports deliberate runtime investigation.
- Completed CLI checks and certification now link verification records to the concrete report, SHA-256 and check completion time.
- Exact repeated knowledge promotion reuses the current record; explicit successors retain separate provenance. Workflow contract v9 and onboarding describe the compact route and discourage redundant checks.

- N4.4 resolves workspace-local mark storage via `git rev-parse --git-path` so linked Git worktrees have independent exact marks.
- N4.3 introduces one bounded archive reader/extractor for preservation and replay, rejecting duplicate/unsafe/symlink/encrypted members and enforcing uncompressed resource budgets.
- N4.2 portable-state safety excludes obvious credential/key material from non-development snapshots by default and adds project-specific `preserve.exclude` patterns without conflating preservation with `.gitignore`.
- N4.1 skeptic hardening makes GitHub CI run the complete `unittest discover` suite used by release qualification instead of a manually maintained subset.
- Reusable `fileSet` certification now binds captured generated outputs to suite-level evidence and re-executes the complete file set when those outputs are missing, stale, or tampered.

## 0.7.0.1-public-onboarding-hotfix

- makes all default interactive bootstrap prompts English; multilingual intent parsing remains supported;
- adds public author/contact metadata for Nikolay Laptev (`Utundry`, `caveboy@yandex.ru`);
- updates GitHub Actions to Node 24-compatible `actions/checkout@v7` and `actions/setup-python@v7`;
- adds a regression test that verifies the distributed bootstrap kit contains the English prompts and no Russian default onboarding prompt.

## 0.7.0-stageM2-public-readiness

- promotes the Stage M1 adaptive bootstrap into the public source tree;
- empty workspaces bootstrap universal/non-development first and specialize only after intent/stack evidence;
- existing repositories infer stack before asking the user;
- adds promptless single-file bootstrap distribution;
- adds public README, MIT license, contribution/security policy, GitHub CI, and public onboarding contract;
- adds clean-room adaptive-onboarding regression tests.

## 0.4.2-stageD1-dogfood-hardening

- Preserve `requiredPaths` and required-path budget omission state in `context affected --json`; JSON is now a lossless scope projection for direct changed paths versus graph-derived preferred paths.
- Make release base project-root discovery deterministic for vendored layouts: the unique shallowest `agent-tools.json` wins, while equally shallow candidates remain fail-safe ambiguous. This keeps consumer project configuration authoritative over a nested vendored Agent DevTools config.
- Re-ran the the original production consumer D1 self-test isolation reproducer on a clean source tree. Aggregate discovery completes 97/97 and B1 release-foundation tests complete repeatedly; no portable source workaround or permanent test splitting was introduced for the environment-specific prior stall.
- Keep all fixes lightweight and dependency-free; no new runtime service or parser dependency.

## 0.4.1-stageD1-semantic-extractor-foundation

- Replaces language-specific declaration regex handling with a normalized semantic IR (`SemanticDocument`, `SemanticDefinition`, `SemanticRelation`) and lightweight extractor families.
- Adds declarative brace-language profiles so modifiers such as `final`, `public`, `static`, `export`, `async`, `readonly`, and similar prefixes do not hide stable natural symbols.
- Adds normalized lightweight one-hop relation facts and a common resolver for local path imports plus uniquely-resolved symbol imports; no AST/runtime dependency is introduced for brace languages.
- Keeps Python on the stdlib AST path while emitting the same normalized relation model.
- Separates directly changed `requiredPaths` from graph-derived `preferredPaths`; affected briefings reserve at least one chunk per indexed changed file when the hard budget permits and report `requiredPathBudgetExceeded` otherwise.
- Adds regression coverage derived from the original production consumer C3 production dogfood: PHP modifier declarations, PHP `use` relation resolution, TypeScript relative imports, and changed-file starvation under a hard budget.
- Unknown languages still degrade safely to anchors/structured selectors/windows; no mandatory parser, Tree-sitter, language server, runtime or external Python dependency is added.

## 0.4.0-stageD1-work-continuity

- Adds zero-service `agent task` state with goal, scope, constraints, definition of done, decisions, blockers, progress, changed-file notes, verification and next step.
- Adds `agent brief` and timeout-oriented `agent resume`, rebuilding work context from the current repository rather than conversation memory.
- Detects stale interrupted Agent DevTools run reports and surfaces the last in-progress stage after a killed/timed-out session.
- Reuses C3 affected-context retrieval in resume packets with a hard context budget.
- Adds portable `.agent-checkpoint.zip` create/inspect/restore with binary-safe changed/untracked payloads, deletions, task state and an internal manifest.
- Safe restore validates Git base when available and refuses divergent target files unless `--force` is explicit.
- Supports checkpoint creation without Git from explicit/task-recorded changed files.
- Keeps continuity local and independent: no server, FSM, lease, daemon, network or new Python dependency.

## 0.3.2.1-stageC3-packaging-hotfix — 2026-09-26

- source packages now embed a deterministic `MANIFEST.sha256` over canonical payload files;
- `scripts/verify_kit.py` is self-contained after extracting a released source ZIP and fails cleanly when the manifest is absent or malformed;
- release verification validates the embedded manifest while keeping it outside canonical source fingerprints;
- exact replay recognizes and verifies the package-only manifest before using a released source ZIP as the next base;
- added regression coverage for embedded source manifests, tamper detection, and next-release base compatibility.

## 0.3.2-stageC3-context-relevance — 2026-09-26

Context assembly now filters before ranking and uses stable semantic addresses without relying on physical line positions.

- natural Python qualified symbols, cheap generic declarations and Markdown headings now receive stable IDs independent of line movement;
- added JSON/TOML/INI structural identities plus source-controlled `agent-context.map.json` with JSON Pointer/TOML/INI/whole-file selectors;
- added `agent context validate` and `agent context rebuild`;
- sidecar changes reindex only old/new mapped targets (plus the map file when normally indexed);
- expanded hard excludes and added configurable low-priority penalties instead of blindly treating archived/history material as normal context;
- retrieval now ranks files first, then chunks inside a bounded candidate file set;
- `context affected` excludes files explicitly classified into unrelated impact domains while keeping unclassified docs eligible;
- direct affected files and lightweight one-hop import relations receive preference;
- near-duplicate/overlapping chunks are suppressed before assembly;
- added source-kind caps and hard deterministic approximate-token budgets (`ceil(chars/4)`);
- query/affected JSON and human output report candidate counts, budget use, provenance, `why` and omission counters;
- added a reusable `context-base` preset component and made ordinary architecture presets inherit it declaratively;
- Agent DevTools dogfoods a real sidecar selector over its own `agent-tools.json`;
- no mandatory external Python dependencies or services were added.

## 0.3.1-stageC2-affected-context — 2026-09-26

Context retrieval now reuses the existing Agent Check impact graph to assemble change-focused briefings without creating a second classifier.

- added `agent context affected` with explicit `--changed` or Git `--base` change discovery;
- reuses normal Check impact expansion, fallback-full behavior, safety guards and suite dependencies;
- retrieval query is derived from effective impact ids, semantic application groups and changed-path terms;
- policy/config absence is reported as `impactAvailable=false` and falls back to path-derived retrieval instead of guessing semantics;
- output remains bounded by the same strict character budget and includes full path/line provenance;
- added self-host affected-context dogfooding and regression tests for known impact, unknown fail-safe and no-Git explicit paths.

## 0.3.0-stageC1-context-index — 2026-09-26

First Context slice. Agent DevTools can now rebuild and query a small repository-local context index without external services or Python dependencies.

- added `agent context ensure/query/inspect/stats`;
- added disposable `.agent-cache/context.sqlite` with atomic `.building` rebuild and `PRAGMA integrity_check`;
- incremental reindexing compares per-file SHA-256 and touches only added/changed/removed files;
- FTS5 is used when available and transparently falls back to stdlib lexical ranking when unavailable;
- added stable cross-language `@semantic-begin` / `@semantic-end` anchors whose identity is independent of current line numbers;
- added cheap Python AST, Markdown heading and PHP/JS/TS/Vue declaration chunks plus universal bounded line windows;
- duplicate/unbalanced semantic anchors fail explicitly instead of silently creating ambiguous identities;
- retrieval returns path/line provenance, source kind/weight, score and explainable match reasons under a strict character budget;
- deleting the context DB is covered as a normal deterministic recovery path;
- Agent DevTools dogfoods the index on its own repository.

## 0.2.1-stageB3-release-presets — 2026-09-26

Release presets now reuse the same small declarative composition engine as checks instead of copying package graphs.

- completed the B2 self-release acceptance gate using the public `agent release` facade;
- added atomic `release-source`, PHP runtime and Vite runtime preset components;
- composite object arrays with stable `id` now merge matching objects recursively, while ordinary arrays keep append-unique semantics;
- `php-vue-vite` composes backend/frontend check components and independent source/runtime release components;
- PHP and Vite standalone presets inherit reusable release declarations instead of embedding them locally;
- Python/Node presets inherit the canonical source-release component;
- `release.artifactPrefix` may be omitted and safely defaults to the project directory basename;
- added regression coverage for merged runtime packages, source-only presets and prefix fallback.

## 0.2.0-stageB1-release-foundation — 2026-09-26

First Release-core slice. `agent release` now builds and verifies deterministic release artifact sets by reusing the already-qualified Check/Replay core.

- added `agent release build --version --base --out-dir`;
- added `agent release verify`;
- release build reuses normal exact replay, including target certification, replay cold certification and generated-byte equality;
- added declarative `release.artifactPrefix` and `release.packages`;
- added `kind=source` packages driven by the canonical `check.replay` source identity;
- added generic `kind=files` packages with include/exclude/required contracts for post-build runtime artifacts;
- deterministic ZIP packages use sorted paths, fixed metadata and `ZIP_STORED`;
- package manifests record exact per-file SHA-256, aggregate content fingerprint, archive hash and byte size;
- release output includes the exact replay bundle, `RELEASE-EVIDENCE.json` and `SHA256SUMS.txt`;
- the staged artifact set verifies itself before atomic directory publication;
- non-empty output directories are rejected instead of mixing stale and new artifacts;
- source package output is proven usable as the base snapshot for the next release;
- Agent DevTools is the first self-host consumer of the portable release facade.

## 0.1.0-stageA6-exact-replay — 2026-09-26

Sixth extraction-parity slice. Agent Check can now prove exact source/generated reproducibility from a known base snapshot without requiring Git.

- added `agent check replay --base ...` with optional deterministic `--bundle-out`;
- added a stdlib-only binary-safe exact replay bundle with fixed ZIP metadata, explicit add/change/delete inventory and payload SHA-256;
- replay verifies the actual base fingerprint before applying any delta and rejects malformed/tampered payloads;
- normal replay incrementally certifies the target, cold-certifies the reconstructed tree and compares declared generated-output bytes;
- `--source-only` provides a fast exact-source proof without semantic execution;
- added `check.replay.sourceInclude`, `sourceExclude` and `sharedPaths`;
- composite presets remain DRY: atomic PHP/Vue components now contribute `vendor` / `frontend/node_modules` replay dependencies to `php-vue-vite`;
- standalone Node/Vue/PHP presets declare the dependency trees needed for replay verification;
- standalone Vue/Vite now declares generated-byte capture explicitly;
- Agent DevTools dogfoods exact replay against the previous A5 source snapshot;
- Stage A extraction parity is closed; Stage B may now reuse the portable replay primitive for release packaging.

## 0.1.0-stageA5-certified-evidence — 2026-09-26

Fifth extraction-parity slice. Certification now has its own portable, transactional PASS-evidence layer instead of relying on ordinary development cache.

- added `agent check certify` and `agent check certify --cold`;
- added opt-in `check.certification.reusableSuites`, global certification inputs and a project-local evidence path;
- certified evidence is promoted only after the complete certification profile passes; failed/interrupted runs leave prior evidence untouched;
- corrupt/missing evidence fails safe to physical execution and is rebuilt only by a successful run;
- reusable file-set suites persist PASS evidence per file, so only changed items need physical re-execution;
- captured generated outputs must still match exact recorded SHA-256 bytes before certified reuse;
- check cache/certification identities now include an automatic fingerprint of the Agent DevTools Python engine;
- simplified managed subprocess logging to direct-to-disk output with bounded post-run tail, removing the reader-thread/pipe race exposed by self-host stress;
- atomic preset components contribute certification suite lists, preserving DRY composition in `php-vue-vite`;
- Agent DevTools self-hosts cold and incremental certification on its own repository.

## 0.1.0-stageA4-file-set-contracts-composite-presets — 2026-09-25

Fourth extraction-parity slice. Generic per-file verification and generated-artifact integrity are now declarative, and complex presets reuse atomic components.

- added serial managed `fileSet` suites using `{file}` / `{file_abs}` with per-file timeout and fail-fast diagnostics;
- added `requires` preconditions plus `outputs.required` / `outputs.capture` postconditions;
- ordinary cache hits now revalidate required outputs and exact captured SHA-256 bytes before reuse;
- added DRY preset component composition with recursive object merge, ordered unique list merge and cycle rejection;
- rewrote `php-vue-vite` as composition of `php-backend` + `vue-vite-frontend`, which both reuse a shared base component;
- formalized feature-by-feature self-hosting and qualified Agent DevTools through its own full cold/warm check;
- retained the original production consumer selection/result parity and zero mandatory external Python dependencies.

## 0.1.0-stageA3-structured-adapters-presets — 2026-09-25

Third extraction-parity slice. Consumer-specific suite output contracts are now declarative and the first architecture preset catalog is operational.

- added result adapters: `exit-code`, `unittest`, `pytest`, `vite`, `json-line`;
- added project-relative `cwd`, environment overrides and `{python}`, `{root}`, `{selected_application_groups_csv}` placeholders;
- cache keys now include adapter/cwd/resolved declared environment identity;
- added representative the original production consumer structured-result acceptance parity tests against the reference consumer;
- added safe declarative `agent preset list/show/apply` with no-overwrite default;
- added presets for Python stdlib, Python+pytest, Node+TypeScript, Vue+Vite, PHP+PHPUnit and split PHP+Vue/Vite;
- documented the remaining consumer-specific mechanisms that must still be extracted before Stage A is complete.

## 0.1.0-stageA2-runner-foundation — 2026-09-25

Second extraction-parity slice. The portable check core can now execute and supervise declarative suites, not only plan them.

- added disposable run workspace with lock, active status, run reports and retention;
- added `agent check run`, `agent check start`, `agent check status` and `agent check cancel`;
- added generic managed subprocess execution with streamed logs, bounded in-memory tail and process-tree timeout handling;
- added content-addressed ordinary stage cache based only on declared inputs, command and tool identity;
- added generic Python/PHP/Node/executable tool identity helper;
- dogfoods Agent DevTools using its own `agent-tools.json` and `agent-check.policy.json`;
- verified cold self-check then warm reuse: both selected suites move from cache miss to cache hit;
- verified detached `start` publishes current stage/process to `status` and clears active state on completion;
- retained exact consumer selection-plan parity across representative change sets.

## 0.1.0-stageA1-foundation — 2026-09-25

First development slice after the migration baseline.

- extracted a portable check-policy engine from the the original production consumer reference consumer;
- preserved exact legacy consumer affected-selection semantics through a declarative consumer safety adapter;
- added compatibility loading for the reference consumer policy format without modifying the legacy files;
- extracted generic SHA-256/stable-fingerprint helpers and atomic JSON I/O;
- extracted a generic certified PASS evidence store whose queued evidence is promoted only on explicit successful flush;
- implemented `agent check plan` with explicit changed paths or Git-based affected discovery.
