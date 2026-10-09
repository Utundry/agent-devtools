# Agent DevTools

Agent DevTools is a lightweight, project-local cognitive and verification runtime for AI agents doing long-lived work.

It is designed around one practical problem: an agent should not have to reconstruct the project, the current task, the relevant evidence, and the verification rules from scratch every time a chat or execution session changes.

The working chain is:

```text
requirement → research/context → finding/assumption → decision → verification → durable knowledge → next step
```

Agent DevTools keeps that chain close to the project, with Python standard library + SQLite only, no mandatory cloud service, daemon, embeddings, vector database, or external Python package.

Current version: **0.15.5**.

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

https://raw.githubusercontent.com/Utundry/agent-devtools/v0.15.5/bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py

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
python devtools/agent/agent.py workflow validate
python devtools/agent/agent.py capabilities --json
```

For ordinary substantial work, the canonical routine inside the **Agent Work Lifecycle** is intentionally small:

```bash
python devtools/agent/agent.py begin --goal "..."
# work with project-native tools; record only meaningful findings/decisions
python devtools/agent/agent.py cognition checkpoint
# if REQUIRED durable candidates are listed:
python devtools/agent/agent.py cognition checkpoint --promote-required
python devtools/agent/agent.py work complete
```

`begin` is not a second workflow engine. It delegates to `work enter`, then immediately builds the durable C3 context projection and reports knowledge health. Primary durable context keeps the strict relevance gate and token budget; weaker but still deterministic cross-task matches may appear separately as at most three `possibly related` cues and do not consume the primary context budget. Do not routinely rescan `.agent-knowledge` after `begin`: the projection is the canonical first-pass semantic retrieval. Use `rg`/`grep`/`find` against durable knowledge only when the projection is insufficient for a specific reason or an exact file/line is required; repository text outside durable knowledge remains normal territory for exact filesystem search. `cognition checkpoint` stays read-only by default; `--promote-required` is an explicit convenience path that promotes only required subject-bearing decisions/requirements through the ordinary `knowledge remember` contract. Advisory findings/assumptions/questions are never auto-promoted.

Semantic capture is intentionally forgiving without adding a second contract: both `cognition finding "..."` and `cognition finding --text "..."` map to the same event primitive. Human cognition output is compact by default (the newly recorded item plus state counts); use `--json` when the complete machine-readable task projection is needed. A natural but invalid guess such as `cognition add` stays invalid and receives an actionable hint to choose a semantic type; it is not added as an alias or second ingestion API. Add `--subject` when the statement is intended to survive as reusable project knowledge. Subjectless cognition remains session-only, and checkpoint explicitly reports when **no promotion is needed**. Routine agents should not call `knowledge promote` before checkpoint; that command remains an expert primitive for exact/manual workflows.

`workflow validate` checks every command advertised by the workflow contract against the real argparse command tree. Machine-readable capabilities also expose `cliCommands`, derived from that same parser rather than from a second command registry.

## What it gives an agent

Agent DevTools is intentionally a harness, not an autonomous platform. Its main capabilities are:

- explicit work sessions with resumable goals and next actions;
- findings, assumptions, decisions, blockers and profile-appropriate research semantics;
- durable project knowledge kept separately from disposable caches;
- repository-aware bounded context retrieval with cross-task lexical isolation so unrelated active knowledge does not consume projection budget on weak term overlap;
- deterministic affected-check planning and project-native verification orchestration;
- certified evidence and cold verification paths;
- exact binary replay from a known source base;
- deterministic source/runtime release packaging;
- portable unfinished-work checkpoints;
- portable non-development workspace snapshots;
- bootstrap/update that keeps runtime disposable and project knowledge tracked.

Typical development workflow:

```bash
python devtools/agent/agent.py begin --goal "..." --next-action "..."
# Do the work with the project's normal tools.
python devtools/agent/agent.py cognition checkpoint
python devtools/agent/agent.py work complete
```

`work enter` remains available as the lower-level lifecycle primitive, but agents should not use it as the routine entrypoint. When `checkpoint` or `work complete` cannot proceed, follow the executable next action printed by the command instead of guessing a lower-level syntax.

For research, verification is similarly guided and this is the canonical route (do not substitute generic `verify record` in routine research):

```bash
python devtools/agent/agent.py verify research
# review the five dimensions shown by the tray; when all genuinely pass:
python devtools/agent/agent.py verify research --confirm-all-pass --summary "..."
# compact --confirm-all-pass is mutually exclusive with the five granular status flags
```

If any research dimension is `warn` or `fail`, use the granular statuses shown by `verify research --json` instead of the compact PASS attestation. Research verification also surfaces current task assumptions and open questions as contextual warnings: they do not automatically block granular PASS, but `unresolved_questions=pass` means any remaining questions were deliberately retained and judged non-blocking. A subject-bearing open question is additionally flagged as potentially material so the agent explicitly checks whether it could change the recommendation or architecture. If the current task has neither recorded source provenance nor evidence, verification also warns before `sourcing=pass`. R6.3 treats missing provenance, potentially material subject-bearing questions, and possible stale cognition as **strong contextual warnings**: while any are present, `--confirm-all-pass` is rejected and the agent must use the existing granular dimension-by-dimension attestation. This does not force a dimension to `warn` or `fail`; it forces the potentially controversial `pass` to be explicit. Subject-bearing assumptions/questions that reuse or refine an earlier subject with different text are surfaced as possible stale cognition; the runtime never silently deletes or supersedes them.

### Exit-code semantics

Agent DevTools exit codes are semantic command results, not a universal boolean success flag. `0` is successful/clean. `1` may be an expected actionable gate or review state when the command documents it (for example `cognition checkpoint` with required promotions); automation should inspect the command contract/output instead of treating every `1` as a crash. `2` denotes an invalid invocation, violated command contract, or execution-contract failure.

### Failure recovery and progress guarantee

External tools are not supervised by Agent DevTools, but routine agents must not retry the same broken action indefinitely. After two equivalent failures of the same tool/action, record the failure:

```bash
python devtools/agent/agent.py cognition tool-failure \
  --tool write \
  --operation "write report" \
  --error 'SchemaError(Missing key at ["content"])' \
  --importance optional \
  --attempts 2 \
  --fallback "Return the report directly to the user."
```

An optional failure is non-blocking: use a fallback or skip the artifact and continue with verification/checkpoint/completion. A mandatory repeated failure becomes an ordinary task blocker, so `work complete` cannot silently succeed until recovery is recorded. The failure is stored as a normal semantic observation with metadata; R4 adds no daemon, supervisor, new database, or interception layer.

### Canonical agent surface

Routine agents use a deliberately small surface: `begin`, `cognition`, profile-appropriate `verify`, `cognition checkpoint`, and `work complete`. Lower-level commands such as `task update`, `work enter`, `work finish`, `knowledge promote`, `checkpoint create`, and `resume` remain available for expert, compatibility, and recovery scenarios but are not the normal path. `begin --profile <current-profile>` is accepted as a compatibility hint, and obvious `task update --add-finding/--add-assumption/--add-decision/--done` aliases are accepted to prevent syntax thrashing without promoting `task update` to the routine surface.

### Bulk semantic capture and final report

Semantic capture accepts exactly one text source: positional text, `--text`, `--stdin`, or `--from-file`. The latter two are intended for multiline Markdown, Unicode text, quotes, `$`, JSON fragments and other content that is awkward to shell-escape:

```bash
cat finding.md | python devtools/agent/agent.py cognition finding --stdin --subject research/nas
python devtools/agent/agent.py cognition decision --from-file decision.md --subject research/nas
```

`python devtools/agent/agent.py work report` is an optional read-only consolidated projection of the current/completed work item: goal, definition of done, summary, decisions, findings, assumptions, requirements, open questions, evidence, blockers, latest verification, knowledge health and semantic-journal status. It does not create a new durable document or modify task state.

### Interruption resume

After a chat/SSE/transport interruption, do not reconstruct work from conversation memory. On the next usable turn run `python devtools/agent/agent.py begin` without `--goal`. An active task resumes with the same task identity, recorded progress and authoritative next action. No new persistence layer is introduced.

Do not memorize syntax from examples. `workflow show`, `capabilities --json`, and CLI `--help` are authoritative for the installed version.

`work complete` is the normal completion action. It validates task/alignment/blocker and durable-knowledge gates, then reuses a successful check only after verifying report integrity, task identity, current inputs/outputs and coverage. Otherwise it runs the affected policy; a clean or unknown tree without usable evidence receives a baseline check. It closes the session only with current PASS evidence. Use `--no-cache` to explicitly request a new execution. Certification, replay and release remain separate tools for deeper validation and delivery. In non-development profiles a fresh explicit verification record is still required. `work finish` is the strict no-execution primitive; routine work does not require choosing it manually.

`workflow show` displays the compact route by default. Use `workflow show --details` for all responsibilities or `--json` for the complete machine-readable contract.

`work enter` is the preferred lifecycle-aware entry action. With an active task it resumes and emits a fresh brief; with `--goal` it starts a new task and uses the routine no-material-gaps fast path by default; with `--handoff <bundle>` it restores verified transferred state. Use `--alignment-pending` for genuinely ambiguous new work. Existing active/restored alignment is preserved unless changed explicitly.

### Preserving unfinished work

Use one profile-aware command instead of remembering the checkpoint/snapshot distinction:

```bash
python devtools/agent/agent.py preserve create
```

Development profiles produce an unfinished-work checkpoint; research, analysis, document, and general profiles produce a portable workspace snapshot. `preserve inspect` and `preserve restore` auto-detect the artifact kind from its internal metadata. Lower-level `checkpoint` and `workspace-snapshot` commands remain available.

### Handoff between chats or agents

Create one portable bundle with both recoverable state and a compact resume briefing:

```bash
python devtools/agent/agent.py handoff create
```

Continue from it with `handoff resume <bundle>`. The handoff wraps the profile-aware `preserve` artifact, verifies all payloads through a SHA-256 manifest, restores through existing conflict-safe rules, and then generates a fresh resume brief. `handoff inspect` verifies without modifying the workspace.

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
