# Structured suite adapters

A verification command and the interpretation of its output are separate concepts. Stage A3 introduces a small declarative `resultAdapter` boundary so consumer-specific output contracts do not leak into the portable runner.

Default behavior remains simple:

```json
"resultAdapter": "exit-code"
```

Bundled adapters are intentionally few:

- `exit-code` — exit code 0 means PASS;
- `unittest` — exit code plus optional `Ran N tests` extraction;
- `pytest` — exit code plus pytest summary counters;
- `vite` — exit code, transformed-module count and optional required generated files;
- `json-line` — a structured JSON result emitted on a prefixed line, with optional format/fatal/allowed-failure rules.

Example structured application suite:

```json
{
  "argv": ["php", "tests/run.php"],
  "env": {
    "AGENT_CHECK_TEST_GROUPS": "{selected_application_groups_csv}"
  },
  "resultAdapter": {
    "kind": "json-line",
    "prefix": "AGENT_CHECK_RESULT ",
    "format": "example-app-test-result",
    "fatalRegex": "(?:PHP\\s+(?:Fatal error|Parse error)|Uncaught\\s+|Traceback|Segmentation fault)",
    "allowedFailures": [
      "Vite index references relative generated assets",
      "Vite build produced JS and CSS assets"
    ],
    "allowNonZeroWithAllowedFailures": true
  }
}
```

The following lightweight placeholders are resolved at execution time:

- `{python}` — the interpreter running Agent DevTools;
- `{root}` — consumer project root;
- `{selected_application_groups_csv}` — comma-separated selected application-group ids.

`cwd` is project-relative and is rejected if it escapes the project root. `env` only adds/overrides declared variables; the consumer process still inherits the ordinary environment.

Adapter configuration, resolved cwd and declared environment overrides are part of the stage cache key. Changing the interpretation contract therefore cannot accidentally reuse stale PASS evidence.
