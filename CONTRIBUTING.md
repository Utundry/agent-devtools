# Contributing

Agent DevTools is intentionally small, local-first and dependency-light. Contributions should preserve those properties rather than turn the project into a service platform.

## Before changing code

1. Read `README.md`, `docs/ARCHITECTURE.md`, and `docs/PORTABILITY-CONTRACT.md`.
2. Run the project's own workflow discovery rather than relying on remembered CLI syntax:

```bash
python agent.py workflow show
python agent.py capabilities --json
```

3. Prefer declarative project behavior over framework-specific branches in the portable engine.
4. Keep disposable state out of durable project knowledge.

## Verification

The minimum pull-request gate is:

```bash
python -m compileall -q agent_devtools tests scripts
# GitHub CI runs the unittest suite in isolated test groups.
python bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py --self-check --json
```

Changes to bootstrap/onboarding should also exercise clean-room empty and existing-project scenarios. Changes to check/release/replay semantics should use the project's own certification/replay path where applicable.

## Scope discipline

Please avoid adding mandatory external services, network dependencies, model-specific APIs, or a second build/test/release pipeline when the same behavior can be expressed through project-native tools and declarative configuration.

Small, explainable changes with explicit verification are preferred over broad speculative abstraction.
