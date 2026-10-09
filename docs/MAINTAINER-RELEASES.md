# Maintainer release and source-update workflow

This document is for contributors and maintainers of the **Agent DevTools source repository**.

It is not part of the normal consumer workflow. A user who installed Agent DevTools into another project normally needs only the bootstrap for initial installation and `self-update` for later runtime updates.

## Declarative source updates

Agent DevTools source releases are prepared from small JSON scenarios instead of one-off publisher scripts.

The stable entry point is:

```bash
python scripts/update_release.py --auto --publish
```

Scenarios are placed under:

```text
.agent-updates/incoming/
```

The updater owns clone isolation, Git safety, qualification, publication, retry, and synchronization. A scenario contains only version intent, optional exact baseline guards, and declarative file transformations.

Scenario format v1 deliberately has no shell or Python hooks. Supported transformations are:

- `replace`
- `write`
- `delete`
- `assert_contains`

`baseBlobs` may pin exact Git blob identities. This makes a stale or incorrectly targeted scenario fail during preflight before expensive release preparation.

Optional `contracts` declarations own explicit public integer contract transitions such as `WORKFLOW_CONTRACT_VERSION: 22 -> 23`. The updater verifies the preimage, applies the declared transition, verifies the target, and rejects stale historical exact-version assertions.

The scenario SHA-256 is recorded in release provenance so retries can validate the same input rather than reconstructing partially applied work.

## Update execution state

Updater execution state is technical and disposable:

```text
.agent-updates/
  incoming/
  applied/
  runs/
```

It may contain scenarios, isolated run directories, logs, copied scenario snapshots, artifacts, and temporary recovery helpers.

This state is not durable project knowledge. Reusable architectural decisions and lessons belong in `.agent-knowledge/`.

## Automatic update chain

`--auto` follows the unique reachable `fromVersion -> toVersion` chain from the current `VERSION`.

Each edge is applied through a fresh Python process loaded from the currently synchronized source tree. This is important for self-updating updater code: a later edge must not be interpreted by stale in-memory code from an earlier version.

Forks and cycles fail closed.

For foreground polling during active maintenance:

```bash
python scripts/update_release.py --periodic 60 --publish
```

Periodic mode is intentionally not a daemon. It launches a fresh current `--auto` process for each actionable cycle and stops with Ctrl+C. An unchanged failed scenario/catalog is suppressed until its content or `VERSION` changes, unless an explicit retry is requested.

For read-only scenario validation:

```bash
python scripts/update_release.py --auto --check
```

This is a diagnostic/preflight mode, not an additional required release step.

## Preflight and qualification

Scenario preflight is intentionally cheap and runs before expensive release preparation. It validates the scenario against a sparse projection containing the touched files, baseline blobs, relevant contracts, version metadata, and any tests needed by contract guards.

A preflight failure must not modify the release working tree.

The release pipeline then performs the repository's configured qualification, build, commit/tag/publish, and synchronization steps. The exact checks are project-owned and may evolve independently of the consumer-facing Agent DevTools workflow.

## Self-hosting

Agent DevTools uses its own released capabilities on its own source when those capabilities are mature enough to do so without circular fake tests.

See:

- `docs/SELF-HOSTING.md`
- `agent-tools.json`
- `.agent-knowledge/`

Self-hosting exists to reduce maintainer routine and dogfood the product. It is not a requirement placed on consumer projects.

## CI and release acceptance

The public repository CI is intended to catch cross-environment regressions in addition to local release qualification. Timing-sensitive tests should validate semantic behavior with sufficient scheduler margin rather than depend on sub-10ms process timing.

Changes to bootstrap/onboarding should exercise clean-room scenarios such as:

1. empty directory → universal workspace → user intent;
2. empty directory + development intent/stack → development specialization;
3. existing supported repository → stack/preset auto-detected;
4. empty research/analysis workspace → remains non-development;
5. recognized but unsupported stack → detected honestly without pretending a generic adapter is correct.

## Consumer update vs source release

These are intentionally different concepts.

A **consumer** updates an installed runtime with:

```bash
python devtools/agent/agent.py self-update
```

A **maintainer** releases Agent DevTools source with the declarative updater/release pipeline described above.

Keeping these paths separate prevents internal release machinery from becoming part of the product's user-facing mental model.
