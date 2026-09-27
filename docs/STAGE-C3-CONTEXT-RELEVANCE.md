# Stage C3 — Context relevance and semantic addressing

Stage C3 keeps `agent context` deliberately small: repository metadata, SQLite/FTS5 when available, deterministic lexical ranking and a hard context budget. It does not add embeddings, a model server or a graph database.

## Stable identity

Line numbers are never semantic identity. Context chunks use the strongest available stable address:

1. explicit `@semantic-begin` / `@semantic-end` markers for important regions that have no natural language symbol;
2. natural symbols such as Python qualified functions/classes and cheap PHP/JS/TS/Vue declarations;
3. structured selectors for JSON/TOML/INI;
4. Markdown heading identities;
5. content windows only as a fallback.

The index always records the current path/line range and content SHA-256 separately from the stable identity.

## Formats without comments

Do not inject fake metadata into application data. JSON/TOML/INI use their own structure. Optional source-controlled `agent-context.map.json` can name a structural region or opaque whole file:

```json
{
  "version": 1,
  "semanticRegions": [
    {
      "id": "frontend.build.command",
      "file": "package.json",
      "selector": {"type": "json-pointer", "value": "/scripts/build"}
    }
  ]
}
```

Supported sidecar selectors are `json-pointer`, `toml-path`, `ini-path` and `whole-file`. The sidecar stores addresses, never copied source content. A sidecar change reindexes only its old/new target files plus the map file itself when that file is part of normal discovery.

`agent context validate` catches duplicate identities, broken explicit markers, missing sidecar targets and invalid selectors.

## Relevance pipeline

Retrieval filters before it assembles context:

```text
repository
  -> hard excludes
  -> affected impact scope when available
  -> file metadata ranking
  -> top candidate files
  -> chunk lexical ranking
  -> one-hop dependency preference
  -> overlap/Jaccard deduplication
  -> source-kind caps
  -> hard approximate token budget
```

`context affected` reuses the exact Agent Check policy. Files that are explicitly classified into unrelated impact areas are excluded. Unclassified documentation remains eligible for lexical retrieval. Directly affected files and one-hop import relations receive a preference bonus. The default dependency depth is one and is capped at three by configuration.

## Budget and explainability

`--budget` is a hard approximate token budget using the deterministic `ceil(chars / 4)` estimator. No tokenizer dependency is required.

Human and JSON output include:

- candidate file/chunk counts;
- estimated tokens versus budget;
- path/current line provenance;
- stable identity and selector where present;
- score reasons (`why`);
- aggregate omission counts for duplicate overlap, source-kind caps and budget pressure.

## Configuration

Example:

```json
{
  "context": {
    "database": ".agent-cache/context.sqlite",
    "semanticMap": "agent-context.map.json",
    "defaultBudget": 3500,
    "candidateFiles": 20,
    "candidateChunks": 80,
    "maxGraphDepth": 1,
    "lowPriority": ["CHANGELOG.md", "docs/archive/**", "releases/**"],
    "kindCaps": {
      "source": 0.6,
      "test": 0.3,
      "architecture": 0.3
    }
  }
}
```

`excluded` sources remain controlled by the ordinary project `ignore` plus `context.exclude`. Low-priority files stay searchable but receive a deterministic ranking penalty.

## Self-hosting acceptance

Agent DevTools must use C3 on itself. A normal C3 qualification performs semantic validation, cold rebuild, warm ensure, bounded query, stable sidecar inspection and `context affected` on the context implementation before `agent check certify` and `agent release`.
