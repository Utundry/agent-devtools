# Stage C1 — lightweight local context index

Stage C1 adds the first usable `agent context` facade while preserving the project's zero-service constraint.

<!-- @semantic-begin context.index.lifecycle -->
The context database is disposable derived state. It contains no unique project knowledge, lives under `.agent-cache/` by default, and may be deleted at any time. Every public query first performs `ensure`, which either reuses the existing schema-compatible index, incrementally reindexes changed files by SHA-256, or atomically rebuilds the database when the index is absent, incompatible or damaged.
<!-- @semantic-end context.index.lifecycle -->

## Commands

```bash
python agent.py context ensure
python agent.py context ensure --rebuild
python agent.py context query "release preset runtime"
python agent.py context inspect context.index.lifecycle
python agent.py context stats
```

## Storage and dependencies

- Python standard library only;
- SQLite from the Python runtime;
- FTS5 is used when available;
- if FTS5 is unavailable, the same index remains usable through a small Python lexical fallback;
- no daemon, server, embeddings, vector database or network call;
- the database is rebuildable from repository text files.

The default database is `.agent-cache/context.sqlite`. Full rebuild is written to `context.sqlite.building`, validated with `PRAGMA integrity_check`, closed, then atomically renamed over the active index.

## Chunk identity

Explicit source anchors are stable across line movement:

```text
@semantic-begin context.index.lifecycle
...
@semantic-end context.index.lifecycle
```

They receive the stable id `semantic:context.index.lifecycle`. Current line numbers are calculated provenance, not identity.

Natural chunks are also extracted cheaply:

- Python class/function ranges via the stdlib `ast` module;
- Markdown sections by headings;
- simple PHP/JS/TS/Vue declaration recognizers;
- bounded overlapping line windows as a universal fallback.

No external parser is required. Unknown text languages still receive fallback windows.

## Incremental lifecycle

`ensure` hashes candidate source files and compares them with the local `files` table. Only added/changed files are re-read and rechunked; removed files are deleted transactionally. Configuration changes intentionally force a full rebuild because include/exclude rules, source weights or chunking rules may have changed.

## Retrieval

Results always include current provenance:

- path;
- current start/end line;
- chunk kind/label;
- semantic anchor when present;
- source kind and configured weight;
- an explainable `why` summary.

The CLI accepts a strict `--max-chars` output budget so retrieval cannot expand without bound. Stage C2 may add impact-aware `context affected`; C1 deliberately stays lexical and local.
