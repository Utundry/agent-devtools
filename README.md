# Agent DevTools

Agent DevTools is a lightweight, project-local memory and context layer for AI agents doing long-lived work.

Its purpose is simple: an agent should not have to reconstruct the project, previous decisions, the current task, and relevant context from scratch every time a chat or execution session changes.

Agent DevTools keeps a compact, inspectable layer of project cognition next to the project itself:

```text
requirement → finding / assumption → decision → durable knowledge → relevant context → next step
```

It uses the Python standard library and SQLite, requires no external service, daemon, vector database, embedding model, account, API key, or mandatory third-party Python package.

## Why use it

Long-running work with an AI agent usually accumulates more context than fits comfortably in one conversation. Important decisions become buried in chat history, temporary observations get confused with lasting project rules, and a new session may need to rediscover things that were already settled.

Agent DevTools gives the agent project-local state that survives the conversation:

- **durable project memory** for decisions and knowledge worth keeping;
- **semantic work history** for meaningful findings, assumptions, requirements, and decisions;
- **task-specific context** so the agent can retrieve a compact relevant subset instead of rereading everything;
- **work continuity** across chats, interruptions, and agent sessions;
- **local-first operation** with no additional infrastructure.

The important distinction is that Agent DevTools is infrastructure **for the agent**. You normally do not need to learn or operate its internal workflow commands yourself.

## 60-second start

### Preferred: give the setup to your agent

Give your AI agent this initialization document:

https://raw.githubusercontent.com/Utundry/agent-devtools/main/AGENT-START-HERE.md

For example:

> Initialize Agent DevTools in the current workspace using this document, then continue my actual task.

The initialization document tells the agent how to install the current release, inspect the generated project instructions, discover the installed workflow, and continue your real task.

### Manual installation

If you prefer to install it yourself, download:

`bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py`

into or next to the project, then run it from the intended workspace root:

```bash
python AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py
```

Bootstrap adapts to the workspace. Existing software projects are inspected before asking for missing information; empty workspaces can be initialized for development, research, analysis, writing, or general work.

After installation, continue working with your agent normally. The installed project instructions and runtime tell the agent how to maintain work state, preserve durable knowledge, and retrieve relevant context.

## Updating

Updating an installed Agent DevTools runtime is one user-facing command:

```bash
python devtools/agent/agent.py self-update
```

The installed runtime is disposable; durable project knowledge remains separate from it.

## What lives in your project

A consumer project normally has three distinct layers:

```text
.agent-knowledge/   durable project knowledge worth keeping
.agent-work/        disposable local work/session state
devtools/agent/     disposable installed Agent DevTools runtime
```

The separation is intentional. Losing disposable runtime/index state should not mean losing durable project knowledge.

Agent DevTools does not become a runtime dependency of the application you are building.

## What the agent does for you

Once installed, the agent uses Agent DevTools to keep long-running work coherent. Depending on the task, it can:

- recover the active goal and unfinished work after a new chat or interruption;
- distinguish temporary observations from knowledge that should survive;
- preserve stable project decisions with provenance;
- retrieve only the durable context relevant to the current task;
- notice unresolved blockers or stale assumptions;
- keep development, research, analysis, and document-oriented work inside the same lightweight project-local model.

The detailed command surface is intentionally documented for agents and advanced users rather than required reading for ordinary users.

## Example uses

Agent DevTools is not limited to software development. The same project-local memory model can support:

- a large software project with many architectural decisions;
- technical or product research that spans multiple sessions;
- hardware/system design where assumptions and evidence evolve;
- document or planning work with durable constraints;
- any long-running agent-assisted project where losing prior reasoning is expensive.

## Design constraints

Agent DevTools deliberately keeps a small trust and resource footprint:

- Python standard library first;
- **zero mandatory external Python dependencies**;
- SQLite only for local structured state where appropriate;
- no required server, daemon, Redis, vector database, embedding model, cloud account, or API key;
- no mandatory network access after installation for ordinary local use;
- durable knowledge is portable and separate from disposable runtime state;
- Git improves development workflows but is not required for basic project memory;
- project-specific build/test/release behavior remains project-specific rather than defining the core product.

The goal is not to turn an agent into a platform. It is to give the agent a small, reliable project-local cognitive layer.

## Documentation

For users and agents:

- `AGENT-START-HERE.md` — canonical handoff for an AI agent initializing Agent DevTools;
- `docs/PUBLIC-ONBOARDING.md` — bootstrap/onboarding behavior;
- `docs/ARCHITECTURE.md` — architecture and portability boundaries;
- `docs/PORTABILITY-CONTRACT.md` — portable-core constraints.

For contributors and maintainers:

- `CONTRIBUTING.md` — development entry point;
- `docs/SELF-HOSTING.md` — how Agent DevTools dogfoods its own capabilities;
- `docs/MAINTAINER-RELEASES.md` — source update and release mechanics;
- `docs/PRESETS.md` — declarative development presets.

## Project status

Agent DevTools is intentionally published as a **0.x project**. It is already used in real development and non-development work, while its internal contracts and agent-facing workflow can continue to evolve explicitly and version-by-version.

## Author and contact

**Nikolay Laptev** (`Utundry`)

Email: `caveboy@yandex.ru`

GitHub: https://github.com/Utundry

For bugs and feature requests, prefer GitHub Issues. Security-sensitive reports should follow `SECURITY.md`.

## License

MIT. See `LICENSE`.
