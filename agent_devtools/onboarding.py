from __future__ import annotations

from pathlib import Path
from typing import Any

AGENTS_REL = Path("AGENTS.md")
AGENTS_BEGIN = "<!-- >>> Agent DevTools workflow >>>"
AGENTS_END = "<!-- <<< Agent DevTools workflow <<< -->"


def managed_block() -> str:
    return "\n".join((
        AGENTS_BEGIN,
        "## Agent DevTools workflow",
        "",
        "This project uses Agent DevTools as the mandatory lightweight work contract for substantial agent work.",
        "",
        "- Before substantial work, run `python devtools/agent/agent.py workflow show`; when syntax or feature availability is uncertain, inspect `python devtools/agent/agent.py capabilities --json` and CLI `--help` instead of guessing.",
        "- Orient first: recover current task/session context and consult existing durable knowledge before changing established behavior.",
        "- Start or continue an explicit work session, then resolve the Task Gap Resolution Gate before substantial execution. High-level `work enter --goal ...` treats a newly created routine task as no-material-gaps by default and continues without a user turn; use `--alignment-pending` when material-gap assessment is genuinely needed. Identify only ambiguities whose wrong choice would materially change the result or cause expensive rework. Never ask a question merely to satisfy the gate. If material gaps exist, offer concrete options and a recommended choice, then obtain explicit user approval or clarification before implementing. Cheap, reversible technical choices may be made autonomously and recorded as assumptions/decisions.",
        "- After task alignment is ready, record meaningful cognition while working. Non-development profiles may use requirements, open questions, evidence, findings, assumptions, and decisions as native work semantics.",
        "- Use profile-appropriate context and verification. Development work should use affected/project-native checks where available; research work should record structured verification and source provenance; other profiles should record explicit verification evidence. Before patch/release handoff, use the Agent DevTools canonical change-set so new untracked project files such as durable knowledge are not lost by plain `git diff`.",
        "- Promote only lasting, reusable project knowledge into `.agent-knowledge/`; zero promotions is the correct outcome for a local task that produced no durable knowledge. Temporary hypotheses and one-off bug details stay session-local. Use stable subjects for significant cognition so active durable knowledge contradictions can be surfaced early; reconcile them explicitly instead of using last-write-wins.",
        "- Finish work only after task alignment is ready, required verification passes, and unresolved blockers are resolved or explicitly retained by the workflow.",
        "- Treat `devtools/agent/`, `.agent-cache/`, `.agent-work/`, and `.agent-bootstrap-report.json` as disposable local runtime/state; keep project knowledge/configuration tracked. Non-development profiles may create portable workspace snapshots for handoff/archival; snapshots must not include disposable runtime/cache trees.",
        "- Agent DevTools orchestrates existing project-native Git/build/test/release/domain tools; do not duplicate or bypass those tools with a parallel workflow.",
        "- If the local runtime is missing, restore/update it with the approved Agent DevTools bootstrap installer before substantial work.",
        "",
        "Project/environment-specific instructions may add constraints, but should not duplicate CLI syntax or contradict this workflow contract.",
        AGENTS_END,
    ))



def action(root: Path) -> str:
    path = root / AGENTS_REL
    if not path.exists():
        return "create"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "update-managed-block"
    block = managed_block()
    if block in text:
        return "unchanged"
    if AGENTS_BEGIN in text or AGENTS_END in text:
        return "update-managed-block"
    return "append"


def ensure(root: Path) -> str:
    path = root / AGENTS_REL
    planned = action(root)
    if planned == "unchanged":
        return planned
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError):
        text = ""
    block = managed_block()
    start = text.find(AGENTS_BEGIN)
    end = text.find(AGENTS_END)
    if start >= 0 and end >= start:
        end += len(AGENTS_END)
        prefix = text[:start].rstrip("\n")
        suffix = text[end:].lstrip("\n")
        updated = "\n\n".join(part for part in (prefix, block, suffix) if part) + "\n"
    else:
        prefix = text.rstrip("\n")
        updated = ((prefix + "\n\n") if prefix else "") + block + "\n"
    path.write_text(updated, encoding="utf-8")
    return planned


def status(root: Path) -> dict[str, Any]:
    return {
        "format": "agent-devtools-agent-onboarding",
        "formatVersion": 1,
        "path": AGENTS_REL.as_posix(),
        "action": action(root),
        "managedBlock": True,
    }
