# Agent DevTools — product specification v0.1

## Purpose

Agent DevTools is a lightweight, reusable development toolbox intended to be copied into the repository of the project being developed. Its consumer is primarily an AI coding agent, but all behavior must remain deterministic and inspectable by a human.

The toolbox addresses three recurring problems:

1. recover the smallest useful project context after a new/lost session;
2. run the right verification with bounded output and reuse trustworthy evidence;
3. create reproducible release evidence and artifacts from a known source base.

## Facades

### `agent check`

Portable verification engine: changed-file discovery, impact classification, selective checks, managed subprocesses, timeouts/cancellation, compact diagnostics, deterministic fingerprints, evidence caching/reuse, full/cold certification, replay verification and generated-artifact comparison.

### `agent release`

Portable release engine: canonical source manifest, exact patch, clean-base replay, build steps, artifact manifest, source snapshot, runtime/package construction, SHA-256 and release evidence. Deployment to a particular server/provider is outside mandatory core and should be a project adapter/hook.

### `agent context`

Local repository retrieval engine: semantic/natural chunks, optional explicit semantic anchors, incremental disposable SQLite index, FTS5 when available with lexical fallback, ranking/deduplication, provenance and strict context budget. No embeddings are required.

## Correctness boundary

Agent DevTools may optimize execution, never weaken the declared verification contract. Unknown classification must fail safe. Cached evidence is reusable only when all declared relevant inputs and tool/environment identities match. Release replay must prove that base + patch reproduces the canonical source target.

## Derived-state rule

All local databases, indexes, run logs and evidence caches are derived state. They must be reconstructible from source/configuration or safely disposable. Unique architectural/project knowledge must live in source, tests, specifications, release evidence or other versioned project files.

## Self-host requirement

A mature facade should be exercised on Agent DevTools itself as soon as doing so provides real verification rather than a circular stub. `agent check` is self-hosted from Stage A4 onward, and its certified-evidence path is self-hosted from Stage A5 onward. `agent release` and `agent context` become self-hosted only after their respective implementations exist.

## Declarative execution contracts

The check facade may express reusable preconditions, file-set execution and output-byte postconditions declaratively. These contracts are included in cache identity and/or cache-hit revalidation so optimization never weakens the declared verification semantics.
