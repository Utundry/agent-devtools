# Declarative presets

Presets are ordinary JSON data shipped with Agent DevTools. They are templates for a consumer project's `agent-tools.json` and `agent-check.policy.json`; the engine contains no framework-specific branches for them.

Commands:

```bash
python agent.py preset list
python agent.py preset show vue-vite
python agent.py preset apply python-stdlib
```

`apply` never overwrites existing files unless `--force` is supplied. A preset may require tools that belong to the consumer project (for example pytest, PHPUnit, TypeScript, Vue or Vite), but Agent DevTools itself still has no mandatory third-party Python dependency.

Bundled presets:

- `python-stdlib` — `unittest` + `compileall`;
- `python-pytest` — pytest + `compileall`;
- `node-typescript` — `tsc --noEmit` + project `npm test`;
- `vue-vite` — `vue-tsc`, project `npm test`, Vite build;
- `php-phpunit` — PHPUnit + `composer validate`;
- `php-vue-vite` — split PHP backend and `frontend/` Vue/Vite application.

Presets deliberately stay small. They are starting policies, not a framework detector and not an attempt to encode every build system. A project should edit the generated declarative files after applying a close preset.

## Preset format

```json
{
  "format": "agent-devtools-preset",
  "formatVersion": 1,
  "id": "python-stdlib",
  "title": "Python stdlib",
  "description": "...",
  "requirements": ["Python 3"],
  "files": {
    "agent-tools.json": {},
    "agent-check.policy.json": {}
  }
}
```

A preset may only write project-relative files and the preset engine rejects path traversal.

## DRY composition

A complex preset may reuse atomic components:

```json
{
  "format": "agent-devtools-preset",
  "formatVersion": 1,
  "id": "php-vue-vite",
  "components": [
    "php-backend",
    "vue-vite-frontend",
    "release-source",
    "release-php-runtime-backend",
    "release-vite-runtime-frontend"
  ],
  "files": {}
}
```

Atomic components live under `presets/components/`. They use the same declarative `requirements` + `files` payload and may themselves include other components (for example the common `base` fragment).

Composition has intentionally tiny semantics: dictionaries merge recursively; ordinary arrays append unique values in order; object arrays whose entries all have stable string `id` values merge matching ids recursively; later scalars override earlier scalars. It is not a templating language and executes no code. Component cycles and unsafe ids are rejected.

`agent preset show <id>` displays the flattened component list and the fully materialized files that `apply` will write.


## Certification composition

Atomic preset components contribute their own `check.certification.reusableSuites` entries. Composite presets merge these lists uniquely in component order, so a stack such as `php-vue-vite` inherits the backend and frontend certification set without copying it. Shared defaults (`profile`, `globalInputs`, evidence path) come from the base component.

## Release composition

Release declarations use the same component system. `release-source` contributes the canonical source package. PHP and Vite runtime components contribute package `id=runtime`; when a complex preset includes more than one runtime component, those rows merge by id into a single package.

`release.artifactPrefix` is optional in generated presets. The release loader falls back to the project directory basename, so portable presets do not need to know the consumer project name. Explicit project configuration may still set a stable prefix.

See `docs/STAGE-B3-RELEASE-PRESETS.md`.

## Context composition

Stage C3 adds one reusable `context-base` component. The shared `base` component inherits it, and standalone architecture presets include it directly. This contributes only declarative context defaults (`.agent-cache/context.sqlite`, bounded candidate counts, one-hop relation depth, low-priority paths and source-kind hints). Complex presets therefore do not copy context policy.

Projects may override these values in their own `agent-tools.json`; the portable context engine contains no architecture-specific branches.
