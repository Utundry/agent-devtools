# Stage B3 — composable release presets

Stage B3 keeps release configuration DRY without introducing a release-specific template language.

The existing preset component mechanism now supports object arrays keyed by a stable `id`: matching objects are merged recursively instead of being duplicated. Ordinary arrays retain the previous append-unique behavior. This is sufficient for independent release components to contribute to the same logical package.

Example composite architecture:

```text
php-vue-vite
├─ php-backend
├─ vue-vite-frontend
├─ release-source
├─ release-php-runtime-backend
└─ release-vite-runtime-frontend
```

The two runtime components both contribute to package `id=runtime`, so the flattened configuration contains one runtime package whose include/required contracts are the union of both atomic fragments.

Bundled release components:

- `release-source` — canonical source package;
- `release-php-runtime-root` — root-level PHP deployment files;
- `release-php-runtime-backend` — split `backend/` PHP deployment files;
- `release-vite-runtime-root` — root-level Vite `dist/` output;
- `release-vite-runtime-frontend` — split `frontend/dist/` output.

`release.artifactPrefix` is optional. When omitted, the project directory basename is used and validated with the same safe identifier rule. This lets reusable presets stay project-name agnostic.

These presets are intentionally examples, not framework knowledge in Python. Projects remain free to edit package include/exclude/required patterns after applying a preset.
