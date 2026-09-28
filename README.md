# Agent DevTools

Agent DevTools is a lightweight, project-local cognitive and verification runtime for AI agents doing long-lived work.

It is designed around one practical problem: an agent should not have to reconstruct the project, the current task, the relevant evidence, and the verification rules from scratch every time a chat or execution session changes.

The working chain is:

```text
requirement → research/context → finding/assumption → decision → verification → durable knowledge → next step
```

Agent DevTools keeps that chain close to the project, with Python standard library + SQLite only, no mandatory cloud service, daemon, embeddings, vector database, or external Python package.

Current version: **0.8.3**.

## 60-second start

### Agent-first start

If you are working with an AI coding, research, or general-purpose agent, give it this **canonical initialization URL**:

https://raw.githubusercontent.com/Utundry/agent-devtools/main/AGENT-START-HERE.md

That document explicitly tells the agent that Agent DevTools is infrastructure for the **current workspace**, not a request to build a site, demo, or project from the repository. It then points the agent to the version-pinned bootstrap and the required post-bootstrap workflow discovery.

In most agent environments, the URL alone should be sufficient. If you want to remove any remaining ambiguity, use:

> Initialize Agent DevTools in the current workspace using the initialization document above, then continue my actual task.

The project repository remains the human-facing entry point:

https://github.com/Utundry/agent-devtools

For manual or automation-oriented installation, the current version-pinned bootstrap is:

https://raw.githubusercontent.com/Utundry/agent-devtools/v0.8.3/bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py

The raw Python bootstrap is an installer URL; the `AGENT-START-HERE.md` URL is the preferred **agent handoff** URL. If the environment cannot access the network, attach the bootstrap file instead.

### The normal path: one bootstrap file

Copy `bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py` into or next to the project and run it from the intended workspace root:

```bash
python AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py
```

You do **not** need to choose an internal Agent DevTools profile first.

Bootstrap is adaptive:

- **Empty project:** it first installs a small universal non-development workspace. It then asks what you plan to do or discuss. If the answer is software development, it asks for stack details only when they are still missing and specializes the existing workspace without discarding accumulated requirements or knowledge.
- **Existing project:** it inspects repository evidence first (`pyproject.toml`, `package.json`, `composer.json`, `Cargo.toml`, `go.mod`, source files, etc.), infers the stack, and does not ask you to repeat information already present in the project.
- **Recognized but unsupported stack:** it reports what was detected and leaves a narrow `needs-development-adapter` state instead of pretending a generic preset is correct.
- **Research / analysis / document work:** it stays non-development and does not install irrelevant build/test policy.

For non-interactive or agent-driven onboarding, the same bootstrap returns structured states such as `needs-intent` or `needs-development-adapter` instead of guessing. The agent can then continue explicitly without exposing internal profiles to the user:

```bash
# First pass in an empty workspace: installs the universal core and reports needs-intent.
python AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py --json

# Continue the same workspace after the user describes development intent/stack.
python AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py \
  --intent "Build a Python command-line tool" \
  --stack "Python stdlib" \
  --json

# For research or analysis, intent alone is normally enough.
python AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py \
  --intent "Research a home backup-power architecture" \
  --json
```

`--intent` and `--stack` are continuation inputs for agents/automation. A human running bootstrap interactively can simply answer the questions instead.

### Updating an existing installation

Once Agent DevTools is installed, updating it is a single semantic action:

```bash
python devtools/agent/agent.py self-update
```

It discovers the release pinned by the canonical handoff, freshly downloads that exact installer, checks its release identity, updates the disposable runtime, and verifies the newly installed `toolVersion`. Use `self-update --check` for a read-only availability check or `self-update --version X.Y.Z` for a deterministic target.

After bootstrap, the agent should read `AGENTS.md` and use the installed runtime as the source of truth:

```bash
python devtools/agent/agent.py workflow show
python devtools/agent/agent.py capabilities --json
```

## What it gives an agent

Agent DevTools is intentionally a harness, not an autonomous platform. Its main capabilities are:

- explicit work sessions with resumable goals and next actions;
- findings, assumptions, decisions, blockers and profile-appropriate research semantics;
- durable project knowledge kept separately from disposable caches;
- repository-aware bounded context retrieval;
- deterministic affected-check planning and project-native verification orchestration;
- certified evidence and cold verification paths;
- exact binary replay from a known source base;
- deterministic source/runtime release packaging;
- portable unfinished-work checkpoints;
- portable non-development workspace snapshots;
- bootstrap/update that keeps runtime disposable and project knowledge tracked.

Typical development workflow:

```bash
python devtools/agent/agent.py work start --goal "..." --next-action "..."
python devtools/agent/agent.py context affected
python devtools/agent/agent.py check plan --profile affected --explain
python devtools/agent/agent.py check run --profile affected
python devtools/agent/agent.py work finish
```

Do not memorize syntax from examples. `workflow show`, `capabilities --json`, and CLI `--help` are authoritative for the installed version.

## Design constraints

Agent DevTools deliberately keeps a small trust and resource footprint:

- Python standard library first; **zero mandatory external Python dependencies**;
- SQLite only for disposable local indexes/evidence where appropriate;
- no required server, daemon, Redis, vector database, embedding model, account, API key, or network access;
- Git improves change discovery but is not required for basic work continuity or exact replay;
- the application being developed never depends on Agent DevTools at runtime;
- project-specific test/build/release behavior belongs in declarative config/adapters;
- `.agent-cache/`, `.agent-work/`, and installed `devtools/agent/` are disposable;
- durable project knowledge is source-controlled and portable;
- safety-critical uncertainty fails explicit rather than silently widening agent authority.

## Empty vs existing projects

The onboarding contract is intentionally asymmetric.

```text
empty workspace
  → universal non-development core
  → ask human intent
  → if development: learn missing stack details
  → specialize

existing workspace
  → inspect repository
  → infer stack and native tooling
  → ask only for genuine gaps
  → specialize if safe
```

This makes the same bootstrap useful for software development, research, analysis, writing, and mixed projects without forcing users to understand Agent DevTools' internal profile model.

## Self-hosting and verification

Agent DevTools dogfoods its own check, context, replay, release, work-continuity, knowledge, and bootstrap capabilities. The public repository CI is intentionally minimal and offline-friendly:

```bash
python -m compileall -q agent_devtools tests scripts
# GitHub CI runs the unittest suite in isolated test groups.
python bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py --self-check --json
```

For the public bootstrap release path, one command prepares and qualifies a new release candidate from a clean tree:

```bash
python scripts/build_release.py --version 0.8.2
```

It bumps the runtime/public version references, refreshes the pinned handoff URL, deterministically rebuilds the bootstrap kit and single-file installer, checks installer release identity, runs the complete unittest suite and compileall, and leaves the resulting release candidate in the working tree for explicit review/commit/tag. It does not push or tag automatically.

When installing from the public handoff, always overwrite a same-named local bootstrap and self-check the freshly downloaded file before execution. A successful runtime update also verifies that the installed runtime version matches the source kit version, not only that their file fingerprints match.

Release-level qualification may additionally run cold certification, exact replay, package verification, and clean-room onboarding scenarios.

## Clean-room acceptance

Before a public release is promoted, these scenarios are expected to pass from the documented bootstrap path:

1. empty directory → universal workspace → `needs-intent`;
2. empty directory + development intent/stack → development specialization;
3. existing supported repository → stack/preset auto-detected without asking the user to repeat it;
4. empty research/analysis workspace → remains non-development;
5. existing recognized but unsupported stack → stack is reported and only missing adapter/check details remain unresolved.

## Repository layout

```text
agent_devtools/     portable Python runtime source
presets/            declarative project presets/components
bootstrap/          single-file bootstrap + embedded minimal kit
scripts/            release/source verification helpers
tests/              stdlib unittest suite
docs/               architecture and stage design notes
agent.py             source-tree CLI entry point
agent-tools.json     self-host configuration
```

A consumer project normally tracks only its configuration/onboarding/knowledge. The installed runtime under `devtools/agent/` is intentionally disposable and may be restored from an approved bootstrap.

## More documentation

Useful starting points:

- `docs/ARCHITECTURE.md` — architecture and portability boundaries;
- `docs/SELF-HOSTING.md` — dogfood/self-release policy;
- `docs/PORTABILITY-CONTRACT.md` — portable-core constraints;
- `docs/PRESETS.md` — declarative development presets;
- `docs/STAGE-D1-WORK-CONTINUITY.md` — session recovery/checkpoints;
- `docs/PUBLIC-ONBOARDING.md` — adaptive bootstrap contract.

## Project status

Agent DevTools is intentionally published as a **0.x project**. The core is already used in real development and non-development work, but the CLI/contracts may continue to evolve while remaining explicit and versioned.

## Author and contact

**Nikolay Laptev** (`Utundry`)

Email: `caveboy@yandex.ru`

GitHub: https://github.com/Utundry

For bugs and feature requests, prefer GitHub Issues so discussion stays visible to the project. Security-sensitive reports should follow `SECURITY.md`.

## License

MIT. See `LICENSE`.
