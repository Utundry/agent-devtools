# Small-project workflow

The normal route is `work enter` → project-native work → `work complete`.
The named phases describe responsibilities, not seven separate CLI calls.
Entry covers orientation, session start and routine no-gap alignment. Assess
material ambiguities before implementation; use `--alignment-pending` when a
wrong choice would change the product or require expensive rework.

Record meaningful findings and decisions as they arise. Read durable knowledge
relevant to the decision being made. Zero knowledge promotions is valid. An
exact repeat of a current record with the same kind, subject, statement,
anchors and source references reuses its identity. Changed statements and
explicit `supersedes` operations remain separate records with their provenance.

## Optional context retrieval

Bootstrap and briefings do not create a context index implicitly. Bootstrap
validates semantic settings without persisting an index. Briefings refresh and
reuse an existing context index.
Use `context ensure`, `context query`, or `context affected` when indexed
retrieval is useful. Explicit semantic comparison through `brief --before`
still builds the indexes needed for that comparison.

Context safety exclusions are retained even when a project's `ignore` list
is empty or custom. Dependencies, Python environments, generated state and
the installed `devtools/agent` tree are excluded. A runtime installed at a
different location inside the project is also excluded by its actual path.
The Agent DevTools source remains indexable when it is itself the project.
The scanner prunes excluded directories before descending into them.

`context.includeRuntime: true` disables the automatic runtime exclusion when
explicitly investigating the tool. Project-specific `ignore` and
`context.exclude` patterns still apply. Changing exclusions changes the
configuration fingerprint, so the next explicit index operation rebuilds
old derived state.

## Verification and repetition

Completed `check run` and `check certify` calls append a verification record
linked to their `report.json`, its SHA-256 and the check completion time. The
report retains suite results, cache state and log paths. Warm results also
reference their original execution report. Deleting an old execution report
marks its details unavailable; changing an existing report invalidates reuse.
Partial runs do not
become completed verification records. This link does not replace the
existing certification and completion gates or make manual declarations
equivalent to machine checks.

Use `work complete` for routine completion. It automatically validates and
reuses current successful evidence without creating another run. When evidence
is absent, stale, corrupted or incomplete, it runs the affected policy using
both current Git changes and task-recorded changed files. If no changed paths
are visible, or Git is unavailable, it verifies the baseline instead of
accepting an empty selection. Stage cache can still reuse unchanged suites.

Use `work complete --no-cache` when physical re-execution is explicitly needed,
or `--resume` to continue compatible interrupted verification. `work finish`
remains strict: it validates existing evidence and never runs checks. The
routine caller no longer needs to choose between these two completion commands.
Alignment, blockers, knowledge consistency and current evidence are still
required. Non-development profiles still require explicit verification records.

Certification, replay and release are separate tools for explicit deeper
verification and delivery. Neither completion nor workflow discovery invokes
them or creates a context index. `workflow show` prints only the compact route;
`--details` exposes all responsibilities and `--json` preserves the full contract.

Machine completion checks report integrity, task/config/engine identity,
affected-suite and application-group coverage, current content hashes and
captured outputs. A manual verification record is an attestation and cannot
replace this proof for a development task. Older unbound reports require one
fresh check with the updated engine.

Check identity hashes all inherited environment variables by default. A suite
may explicitly declare `"cacheEnv": ["PATH", "PYTHON*", "MY_BUILD_FLAG"]`
when those names completely describe its inherited environment dependencies.
Names may use shell-style patterns; a new matching variable also changes the key.
An empty array declares that it does not depend on inherited environment.
Configured command `env` values remain part of execution identity. Environment
values are hashed and are not copied into reports. Prefer precise suite inputs
and environment declarations when unrelated changes cause cache misses.

The reported 208 MB consumer database and compile-cache misses were not
available for forensic reproduction. Regression fixtures cover the confirmed
indexing paths; the consumer's actual cache-key inputs still need inspection
before attributing its misses to a cache defect. The audit did reproduce false
cache hits after changing inherited environment; that defect is now covered by
the environment-identity regression.
