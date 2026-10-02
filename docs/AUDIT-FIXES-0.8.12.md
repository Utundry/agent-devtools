# Audit hardening after 0.8.11

This change addresses the reproduced audit findings while retaining the
standard-library-only runtime. The normal workflow remains `work enter` →
project-native work → `work complete`, or `work finish` after a fresh check.
Indexed retrieval remains optional, and an exact knowledge repeat keeps its
existing identity.

Clean-room dogfooding also exposed an eager `context ensure` in the distributed
bootstrap launcher. That post-install step is removed. The launcher is now
editable source in `bootstrap/BOOTSTRAP_AGENT_DEVTOOLS.py`, which the builder
uses for the derived kit and single-file installer. The project's own check
inputs also include scripts and bootstrap distributions read by its tests.

Replay dogfooding refused the tag's stale package-only manifest. Historical
0.7.0.1 validation and evidence are retained under `docs/archive/`; the root
manifest is no longer tracked. Release packages generate their own current
manifest. The v0.8.11 base used for qualification is reconstituted from canonical
tagged files, each checked byte-for-byte against Git, with a regenerated package
manifest. No manifest verification is bypassed.

The self-host environment rotates proxy variables between command invocations.
Local offline tests/compilation explicitly declare Python, Git, locale, temporary
directory and metadata environment dependencies using `cacheEnv` name patterns.
This is ordinary project configuration; the engine retains full-environment
identity by default. Hard deadlines also participate in execution identity.

| Finding | Change | Regression evidence |
| --- | --- | --- |
| A01 stale/tampered verification | Bind checks to task, engine, configuration, selected coverage and content; validate the stored report hash at completion | Modified/added/deleted inputs, changed config/task, tampered report/output, incomplete affected coverage; unchanged finish does not rerun commands |
| A02 inherited environment absent from keys | Hash the effective environment by default, with an explicit `cacheEnv` contract for narrower keys | Changed relevant values miss; unrelated declared values hit; values are absent from reports |
| A03 state overwritten during restore | Preflight artifact, task and verification conflicts before writing; explicit force retains its meaning | Task-only and verification-only conflicts preserve target state and artifacts |
| A04 recursive glob mismatch | Shared matcher treats `**/` as zero or more directories | Root Python files and `src/*.py` participate in `**/*.py` and `src/**/*.py` respectively |
| A05 stale research context | Refresh an existing disposable index before briefing retrieval | Replaced source marker appears and the old marker disappears |
| A06 repeated archive includes itself | Exclude the exact output/temp paths and branded preservation archives | Repeated snapshot has stable artifact count/bytes; ordinary user ZIP remains included |
| A07 memory/size-limit overhead | Stream snapshot and handoff payloads and hashes; preflight file sizes; use a mode lookup map | Oversized-file rejection and large-payload inspect/restore allocation bounds |
| A08 archive aliases | Canonicalize names and reject aliases/file-directory collisions before extraction | `a/b`, `a/./b`, `a//b` and prefix-collision fixtures fail without extraction |
| A09 repeated ignored-tree traversal | Shared walker prunes excluded directories and fixed glob prefixes; cache hashes live only within a run | Ignored dependency subtree is never visited |
| A10 process polling floor | Wait for exit with bounded, deadline-aware polling | Existing timeout/chunk/process tests remain active; timing is measured separately |
| A11 warm result loses provenance | Store original report path/hash in cache and certification evidence, expose availability/integrity | Warm result references cold report; missing details stay explicit; corrupted details prevent reuse |
| A12 generic intent misclassification | Generic service/develop/build terms no longer imply code; research context and token boundaries matter | Business, clinical-analysis and research goals avoid stack questions; explicit Python API still selects Python |
| A13 compact route hidden | Show the routine route in ordinary workflow output | Plain CLI output reviewed; workflow contract v9 remains available |

The legacy briefing `workingStateFingerprint` remains as a compatibility alias
for `stateMetadataFingerprint`, with `fingerprintKind: metadata`. It is not used
as proof that source contents are unchanged. Machine completion uses hashes.

The original consumer's 208 MB database was not available for forensic review.
Runtime exclusion, lazy index creation and ignored-tree pruning are verified
with repository fixtures. Cache-miss causes in that separate consumer cannot
be inferred from its reported timing alone.

Qualification uses the real project CLI and normal contracts: cold/warm check,
cold/incremental certification, context ensure/query/affected/inspect, exact
replay from v0.8.11, release build/verify and bootstrap self-check. No repository
name exception, alternate runner or new database is introduced.
