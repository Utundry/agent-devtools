# Stage A3 — structured adapters and presets

Stage A3 moves another consumer-specific boundary out of the the original production consumer reference consumer without redesigning the proven selection engine.

Implemented:

1. reusable suite-result adapters (`exit-code`, `unittest`, `pytest`, `vite`, `json-line`);
2. project-relative `cwd` and declarative environment overrides;
3. portable execution placeholders including selected application groups;
4. result-adapter/cwd/env identity in development cache keys;
5. the original production consumer structured application-result acceptance parity for representative PASS, expected source-only omission, unexpected failure and fatal-output cases;
6. declarative preset catalog plus safe `list/show/apply` CLI;
7. six initial architecture presets.

Still intentionally consumer-specific and deferred to the next extraction slice:

- frontend dependency verification against the release manifest;
- parallel PHP file-set lint with per-file evidence;
- Vite pre-clean + generated-output provenance comparison;
- release metadata checks and exact replay/certification orchestration.

Those mechanisms should be extracted as generic file-set/precondition/postcondition/replay primitives rather than copied as the original production consumer branches.
