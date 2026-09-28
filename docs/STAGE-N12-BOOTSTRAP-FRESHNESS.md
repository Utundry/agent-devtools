# Stage N1.2 — Bootstrap Freshness & Runtime Identity

## Finding

Dogfood of the public v0.8.1 handoff exposed a freshness gap at the boundary between the pinned handoff URL and local execution.

The handoff pointed to v0.8.1, but an agent reused an already-downloaded same-named v0.8.0 installer. That old installer correctly verified its own embedded v0.8.0 payload and correctly decided that the already-installed v0.8.0 runtime was unchanged. The internal fingerprint chain was sound; the wrong artifact entered the chain.

## Invariants

The public installation/update path now distinguishes two questions:

```text
Is this installer the intended release?
        ↓
Does the installed runtime exactly match this installer's source kit?
```

Both must be true.

The handoff therefore requires a fresh overwrite of the local installer and a self-check before execution. Same filename is never evidence of freshness.

Generated single-file installers expose an explicit `releaseVersion`. They also accept:

```bash
python AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py \
  --self-check \
  --expect-version 0.8.2 \
  --json
```

A version mismatch fails closed before the embedded launcher is executed.

Runtime installation/update additionally compares the version embedded in `agent_devtools/__init__.py` on both the source-kit side and the installed `devtools/agent` side. Fingerprint equality remains required; version equality is an additional provenance check rather than a replacement.

## One-command release preparation

Public release preparation is now:

```bash
python scripts/build_release.py --version 0.8.2
```

From a clean Git tree this command:

1. validates that the target tag does not already exist;
2. bumps `agent_devtools.__version__`, README current version, `VERSION`, and pinned bootstrap handoff URLs;
3. deterministically rebuilds the bootstrap kit and single-file installer;
4. verifies deterministic rebuild parity;
5. self-checks the installer against the requested release version;
6. runs the complete unittest suite;
7. runs compileall and `git diff --check`;
8. leaves the qualified release candidate in the working tree for explicit review and publication.

The command is transactional for the files it manages: if preparation or qualification fails, those files are restored to their pre-run bytes.

It deliberately does not commit, tag, or push. Publication remains an explicit irreversible action after inspecting the candidate.

## Principle

**A valid payload is not enough; the artifact carrying that payload must also be the release the user intended to execute.**
