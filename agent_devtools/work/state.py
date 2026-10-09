from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_devtools.core.io import atomic_json_write
from agent_devtools.core.workspace import default_work_root

TASK_FORMAT = "agent-devtools-task-state"
TASK_VERSION = 1


class TaskStateError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task_state_path(root: Path) -> Path:
    return default_work_root(root) / "task.json"


def _normalize_rel(value: str) -> str:
    value = value.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _strings(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for raw in values or ():
        value = str(raw).strip()
        if value and value not in result:
            result.append(value)
    return result


def _base_state(goal: str) -> dict[str, Any]:
    now = utc_now()
    return {
        "format": TASK_FORMAT,
        "formatVersion": TASK_VERSION,
        "taskId": str(uuid.uuid4()),
        "goal": goal.strip(),
        "scope": [],
        "constraints": [],
        "definitionOfDone": [],
        "decisions": [],
        "findings": [],
        "assumptions": [],
        "requirements": [],
        "openQuestions": [],
        "evidence": [],
        "blockers": [],
        "changedFiles": [],
        "verification": [],
        "taskAlignment": {
            "status": "pending",
            "materialGaps": [],
            "proposal": "",
            "resolution": "",
            "explicitUserApproval": False,
            "updatedAtUtc": now,
        },
        "summary": "",
        "nextStep": "",
        "status": "active",
        "startedAtUtc": now,
        "updatedAtUtc": now,
        "completedAtUtc": None,
    }


def validate_task_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TaskStateError("task state must be a JSON object")
    if payload.get("format") != TASK_FORMAT or int(payload.get("formatVersion") or 0) != TASK_VERSION:
        raise TaskStateError("unsupported task state format/version")
    goal = str(payload.get("goal") or "").strip()
    if not goal:
        raise TaskStateError("task state goal is empty")
    clean = dict(payload)
    clean["goal"] = goal
    for key in ("scope", "constraints", "definitionOfDone", "decisions", "findings", "assumptions", "requirements", "openQuestions", "evidence", "blockers", "changedFiles", "verification"):
        value = clean.get(key, [])
        if not isinstance(value, list):
            raise TaskStateError(f"task state {key} must be an array")
        clean[key] = _strings(str(item) for item in value)
    clean["summary"] = str(clean.get("summary") or "").strip()
    raw_alignment = clean.get("taskAlignment")
    if raw_alignment is None:
        # Backward compatibility for task.json created before the task-gap gate.
        # Existing sessions must remain resumable after a runtime update.
        raw_alignment = {
            "status": "ready",
            "materialGaps": [],
            "proposal": "",
            "resolution": "legacy task state created before task-gap alignment",
            "explicitUserApproval": False,
            "updatedAtUtc": str(clean.get("updatedAtUtc") or ""),
        }
    if not isinstance(raw_alignment, dict):
        raise TaskStateError("task state taskAlignment must be an object")
    alignment = dict(raw_alignment)
    alignment_status = str(alignment.get("status") or "pending").strip()
    if alignment_status not in {"pending", "clarification-required", "ready"}:
        raise TaskStateError("task alignment status must be pending, clarification-required, or ready")
    gaps = alignment.get("materialGaps", [])
    if not isinstance(gaps, list):
        raise TaskStateError("task alignment materialGaps must be an array")
    alignment["status"] = alignment_status
    alignment["materialGaps"] = _strings(str(item) for item in gaps)
    alignment["proposal"] = str(alignment.get("proposal") or "").strip()
    alignment["resolution"] = str(alignment.get("resolution") or "").strip()
    alignment["explicitUserApproval"] = bool(alignment.get("explicitUserApproval", False))
    alignment["updatedAtUtc"] = str(alignment.get("updatedAtUtc") or clean.get("updatedAtUtc") or "")
    clean["taskAlignment"] = alignment
    status = str(clean.get("status") or "active").strip()
    if status not in {"active", "completed"}:
        raise TaskStateError("task state status must be active or completed")
    clean["status"] = status
    clean["completedAtUtc"] = clean.get("completedAtUtc") or None
    clean["nextStep"] = str(clean.get("nextStep") or "").strip()
    return clean


def task_alignment_ready(state: dict[str, Any]) -> bool:
    alignment = state.get("taskAlignment")
    return isinstance(alignment, dict) and str(alignment.get("status") or "") == "ready"


def align_task(
    root: Path,
    *,
    material_gaps: Iterable[str] = (),
    proposal: str = "",
    user_approved: bool = False,
    no_material_gaps: bool = False,
    resolution: str = "",
) -> dict[str, Any]:
    """Resolve the lightweight task-gap gate before substantial execution."""
    state = load_task_state(root)
    if state is None:
        raise TaskStateError("no task state exists; use work start first")
    gaps = _strings(material_gaps)
    modes = int(bool(gaps)) + int(bool(user_approved)) + int(bool(no_material_gaps))
    if modes != 1:
        raise TaskStateError("choose exactly one alignment action: material gaps, user approval, or no material gaps")
    now = utc_now()
    current = dict(state.get("taskAlignment") or {})
    if gaps:
        proposed = proposal.strip()
        if not proposed:
            raise TaskStateError("material task gaps require a concrete proposal before asking the user")
        state["taskAlignment"] = {
            "status": "clarification-required",
            "materialGaps": gaps,
            "proposal": proposed,
            "resolution": "",
            "explicitUserApproval": False,
            "updatedAtUtc": now,
        }
    elif user_approved:
        if str(current.get("status") or "") != "clarification-required" or not current.get("materialGaps"):
            raise TaskStateError("user approval requires previously recorded material task gaps")
        resolved = resolution.strip()
        if not resolved:
            raise TaskStateError("user approval requires a compact resolution summary")
        current.update({
            "status": "ready",
            "resolution": resolved,
            "explicitUserApproval": True,
            "updatedAtUtc": now,
        })
        state["taskAlignment"] = current
    else:
        resolved = resolution.strip() or (
            "No material task gaps identified; proceed without user clarification"
        )
        state["taskAlignment"] = {
            "status": "ready",
            "materialGaps": [],
            "proposal": "",
            "resolution": resolved,
            "explicitUserApproval": False,
            "updatedAtUtc": now,
        }
    state["updatedAtUtc"] = now
    atomic_json_write(task_state_path(root), state)
    return validate_task_state(state)


def load_task_state(root: Path) -> dict[str, Any] | None:
    path = task_state_path(root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskStateError(f"cannot read task state: {exc}") from exc
    return validate_task_state(payload)


def start_task(
    root: Path,
    *,
    goal: str,
    scope: Iterable[str] = (),
    constraints: Iterable[str] = (),
    definition_of_done: Iterable[str] = (),
    next_step: str = "",
    replace: bool = False,
) -> dict[str, Any]:
    path = task_state_path(root)
    if path.exists() and not replace:
        raise TaskStateError("task state already exists; use task update or task start --replace")
    state = _base_state(goal)
    state["scope"] = _strings(scope)
    state["constraints"] = _strings(constraints)
    state["definitionOfDone"] = _strings(definition_of_done)
    state["nextStep"] = next_step.strip()
    atomic_json_write(path, state)
    return state


def update_task(
    root: Path,
    *,
    goal: str | None = None,
    add_scope: Iterable[str] = (),
    add_constraints: Iterable[str] = (),
    add_done: Iterable[str] = (),
    add_decisions: Iterable[str] = (),
    add_findings: Iterable[str] = (),
    add_assumptions: Iterable[str] = (),
    add_requirements: Iterable[str] = (),
    add_open_questions: Iterable[str] = (),
    add_evidence: Iterable[str] = (),
    add_blockers: Iterable[str] = (),
    changed_files: Iterable[str] = (),
    verification: Iterable[str] = (),
    resolve_blockers: Iterable[str] = (),
    summary: str | None = None,
    next_step: str | None = None,
    semantic_subjects: dict[str, str] | None = None,
    semantic_source: str = "task.update",
) -> dict[str, Any]:
    state = load_task_state(root)
    if state is None:
        raise TaskStateError("no task state exists; use task start first")
    if goal is not None:
        value = goal.strip()
        if not value:
            raise TaskStateError("task goal cannot be empty")
        if value != state.get("goal"):
            state["goal"] = value
            state["taskAlignment"] = {
                "status": "pending",
                "materialGaps": [],
                "proposal": "",
                "resolution": "goal changed after prior alignment",
                "explicitUserApproval": False,
                "updatedAtUtc": utc_now(),
            }
    additions = {
        "scope": add_scope,
        "constraints": add_constraints,
        "definitionOfDone": add_done,
        "decisions": add_decisions,
        "findings": add_findings,
        "assumptions": add_assumptions,
        "requirements": add_requirements,
        "openQuestions": add_open_questions,
        "evidence": add_evidence,
        "blockers": add_blockers,
        "changedFiles": (_normalize_rel(str(item)) for item in changed_files),
        "verification": verification,
    }
    semantic_kind = {
        "decisions": "decision",
        "findings": "finding",
        "assumptions": "assumption",
        "requirements": "requirement",
        "openQuestions": "question",
        "evidence": "evidence",
        "blockers": "blocker",
        "verification": "verification",
    }
    new_events: list[tuple[str, str, str]] = []
    subjects = semantic_subjects or {}
    for key, values in additions.items():
        previous = list(state.get(key, []))
        incoming = list(values)
        merged = _strings([*previous, *incoming])
        state[key] = merged
        if key in semantic_kind:
            for item in merged:
                if item not in previous:
                    new_events.append((semantic_kind[key], item, str(subjects.get(key) or "").strip()))
    resolved = set(_strings(resolve_blockers))
    if resolved:
        state["blockers"] = [item for item in state.get("blockers", []) if item not in resolved]
    if summary is not None:
        state["summary"] = summary.strip()
    if next_step is not None:
        state["nextStep"] = next_step.strip()
    state["updatedAtUtc"] = utc_now()
    previous_state = load_task_state(root)
    atomic_json_write(task_state_path(root), state)
    event_source = str(semantic_source or "task.update").strip() or "task.update"
    journal_events = [
        {"kind": kind, "text": text, "subject": subject, "metadata": {"source": event_source}}
        for kind, text, subject in new_events
    ]
    if resolved:
        previous_blockers = set((previous_state or {}).get("blockers", []))
        for item in sorted(resolved & previous_blockers):
            journal_events.append({
                "kind": "blocker",
                "text": item,
                "metadata": {"source": "task.update", "action": "resolved"},
            })
    if journal_events:
        from .journal import SemanticJournalError, append_events
        try:
            append_events(
                root,
                task_id=str(state.get("taskId") or ""),
                events=journal_events,
                created_at_utc=state["updatedAtUtc"],
            )
        except SemanticJournalError as exc:
            if previous_state is not None:
                atomic_json_write(task_state_path(root), previous_state)
            raise TaskStateError(f"cannot persist semantic journal; task state rolled back: {exc}") from exc
    return state


def clear_task(root: Path) -> bool:
    path = task_state_path(root)
    if not path.exists():
        return False
    path.unlink()
    return True


def complete_task(root: Path, *, summary: str | None = None) -> dict[str, Any]:
    state = load_task_state(root)
    if state is None:
        raise TaskStateError("no task state exists; use task start first")
    if state.get("blockers"):
        raise TaskStateError("cannot finish task with unresolved blockers")
    if not task_alignment_ready(state):
        raise TaskStateError("cannot finish task before the task-gap alignment gate is ready")
    if summary is not None:
        state["summary"] = summary.strip()
    state["status"] = "completed"
    state["nextStep"] = ""
    state["completedAtUtc"] = utc_now()
    state["updatedAtUtc"] = state["completedAtUtc"]
    atomic_json_write(task_state_path(root), state)
    return state
