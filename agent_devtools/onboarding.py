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
        "- Read this managed workflow block, then use `begin` once as the normal initial entrypoint. Do not rerun `begin` for ordinary user follow-ups or clarifications while the active task remains intact; rerun `begin` without `--goal` only after a real interruption/lost turn or when project-state recovery is needed. Do not run `workflow show` or `capabilities --json` as a routine preamble when the normal surface is already known; use them or CLI `--help` only when syntax, feature availability, profile state, or recovery behavior is actually uncertain.",
        "- Canonical routine: `begin` -> project-native work -> `cognition checkpoint` when semantic closeout needs review -> `work complete`. `begin` already delegates to the lifecycle entry primitive and projects durable context. Do not start routine work with `work enter`; use it only when debugging or integrating the lifecycle primitive. Completion errors must be followed using the executable next action they provide instead of guessing lower-level syntax.",
        "- Orient first through `begin`: recover current task/session context and consult existing durable knowledge before changing established behavior. In a fresh clone, missing `.agent-work` is normal; Git-tracked `.agent-knowledge` is the durable project memory.",
        "- Start or continue an explicit work session through `begin`, then resolve the Task Gap Resolution Gate before substantial execution. `begin --goal ...` treats a newly created routine task as no-material-gaps by default and continues without a user turn; use `--alignment-pending` when material-gap assessment is genuinely needed. If `begin` reports alignment ready, do not run `work align` as a separate ritual. `work align --user-approved` is valid only after a material gap was recorded and the state became clarification-required; ordinary user approval or clarification does not itself require that transition. Identify only ambiguities whose wrong choice would materially change the result or cause expensive rework. Never ask a question merely to satisfy the gate. If material gaps exist, offer concrete options and a recommended choice, then obtain explicit user approval or clarification before implementing. Cheap, reversible technical choices may be made autonomously and recorded as assumptions/decisions.",
        "- Normal surface: for routine work use only `begin`, `cognition`, profile-appropriate `verify`, `cognition checkpoint`, and `work complete`. Do not explore advanced primitives just because they appear in `--help`.",
        "- Do not use `task update` for normal findings/decisions/assumptions; use `cognition`. Do not use `work enter` instead of `begin`. Do not use `work finish` for routine completion; after verification run `cognition checkpoint` then `work complete`. Do not call `knowledge promote` directly in routine work.",
        "- After task alignment is ready, record meaningful cognition while working. When two or more meaningful semantic events are ready together, prefer `cognition batch` so one CLI invocation preserves them as separate typed events; use a single cognition command for an isolated event rather than artificially delaying it. Semantic text may be passed positionally, through `--text`, from UTF-8 stdin with `--stdin`, or from a UTF-8 file with `--from-file`; use exactly one source. Add a stable `--subject` at capture time only when the statement is intended to be reusable/durable; subjectless cognition is deliberately session-local.",
        "- After completion, `work report` is an optional read-only consolidated projection of goal, DoD, findings, decisions, open questions, verification and knowledge state. It is not a checkpoint, does not create a second source of truth, and is not an additional mandatory lifecycle step.",
        "- Use profile-appropriate context and verification. Development work should use affected/project-native checks where available. Research work must use the guided `python devtools/agent/agent.py verify research` route rather than guessing generic `verify record`; other profiles should record explicit verification evidence. Before patch/release handoff, use the Agent DevTools canonical change-set so new untracked project files such as durable knowledge are not lost by plain `git diff`.",
        "- Semantic checkpoint is the routine authority for durable-memory classification. Do not call `knowledge promote` directly in routine work before checkpoint. REQUIRED decisions/requirements are handled through `cognition checkpoint --promote-required`; advisory subject-bearing events may be intentionally remembered by event id after checkpoint; session-only events require no promotion. zero promotions is a correct and explicit result.",
        "- Tool Failure Recovery: if the same tool/action failure repeats twice, stop retrying that exact action and run `cognition tool-failure` with the observed attempts. Do not loop. Classify the failed step as `optional` or `mandatory`: optional work must use a fallback or be skipped without blocking the task; mandatory work becomes an ordinary blocker until a successful fallback resolves it. Always preserve already completed findings, evidence, decisions, and verification instead of restarting the task because a later tool failed.",
        "- Interruption Resume: after any transport/chat/SSE interruption or lost agent turn, run `begin` without `--goal` before substantive continuation. Resume the existing active task from project state and its returned next action. Do not reconstruct progress from conversation memory; project task state is authoritative. Preserve already completed cognition, verification, and blockers exactly as recorded.",
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
