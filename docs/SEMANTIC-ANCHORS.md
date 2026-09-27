# Semantic addressing for `agent context`

Line numbers are navigation output, not stable identity.

Use the strongest natural identity available. Explicit markers are intentionally sparse and are only needed when an important logical region does not coincide with a natural language symbol.

For comment-capable formats:

```text
// @semantic-begin ref.person.active-entrepreneur
...
// @semantic-end ref.person.active-entrepreneur
```

The same marker text works inside the native comment syntax of Python (`#`), SQL (`--`), HTML/Vue (`<!-- -->`) and similar formats. Raw marker examples in prose/code fences are not treated as anchors because the parser requires a comment prefix.

For formats where comments are unavailable or undesirable, do not mutate application data. Use structural identities instead:

```text
JSON: package.json#/scripts/build
TOML: pyproject.toml::tool.pytest.ini_options
INI:  config.ini::database.host
```

When a human-readable cross-file concept name is useful, add an optional source-controlled `agent-context.map.json` sidecar mapping the semantic ID to a `json-pointer`, `toml-path`, `ini-path` or `whole-file` selector.

A chunk identity combines:

```text
stable id:    explicit semantic id / natural symbol / structural selector / heading
location:     current path + calculated line range
integrity:    content SHA-256
```

Rules:

- markers and sidecar entries are optional;
- semantic IDs are human-readable and stable after publication;
- duplicate IDs, unmatched boundaries and invalid nesting are errors;
- sidecar selectors must resolve to an existing project file/value;
- line ranges are recalculated whenever a file is reindexed;
- the SQLite index never owns unique knowledge;
- `agent context validate` verifies source-controlled semantic metadata without making it authoritative runtime state.
