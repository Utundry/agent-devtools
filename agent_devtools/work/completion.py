from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from agent_devtools.check import cli as check_cli
from agent_devtools.check.changes import git_changed_files
from agent_devtools.profiles import ProfileError, load_profile
from agent_devtools.check.freshness import validated_check_report
from .knowledge import validate_knowledge
from .semantic_closeout import require_semantic_closeout
from .state import TaskStateError, complete_task, load_task_state, task_alignment_ready
from .verification import latest_verification

_PASS_STATUSES = {"pass", "passed", "success", "ok"}

def _require_ready_task(root: Path) -> dict[str, Any]:
    state = load_task_state(root)
    if state is None:
        raise TaskStateError("no task state exists; use work start first")
    if state.get("blockers"):
        raise TaskStateError("cannot complete work with unresolved blockers")
    if not task_alignment_ready(state):
        raise TaskStateError("cannot complete work before the task-gap alignment gate is ready")
    return state

def _require_fresh_success(state: dict[str, Any], latest: dict[str, Any] | None, noun: str) -> dict[str, Any]:
    if not latest or str(latest.get("status") or "").lower() not in _PASS_STATUSES:
        raise TaskStateError(f"cannot complete work without a successful {noun}")
    completed = str(latest.get("completedAtUtc") or "")
    started = str(state.get("startedAtUtc") or "")
    if not completed or (started and completed < started):
        raise TaskStateError("cannot complete work with verification older than the current task")
    return latest

def _run_development_verification(root: Path, *, no_cache: bool, resume: bool) -> tuple[int, str]:
    changed = git_changed_files(root)
    task = load_task_state(root) or {}
    known = changed | set(task.get("changedFiles", [])) if changed is not None else None
    args = argparse.Namespace(
        profile="affected", base=None, changed=sorted(known or []), config=None,
        no_cache=no_cache, resume=resume, max_chunks=None,
        time_slice_seconds=None, json_output=False, minimum_baseline=known == set(),
    )
    output = io.StringIO()
    with redirect_stdout(output):
        code = check_cli.command_run(root, args)
    return code, output.getvalue().strip()

def complete_work(
    root: Path,
    *,
    summary: str | None = None,
    run_verification: bool = True,
    no_cache: bool = False,
    resume: bool = False,
) -> dict[str, Any]:
    state = _require_ready_task(root)
    knowledge = validate_knowledge(root)
    if not knowledge.get("ok"):
        raise TaskStateError(
            "cannot complete work while durable knowledge has conflicts or dangling supersedes"
        )
    semantic = require_semantic_closeout(root)
    try:
        profile = load_profile(root)
    except ProfileError as exc:
        raise TaskStateError(str(exc)) from exc

    verification_performed = False
    verification_output = ""
    verification_action = "recorded"
    verification_reason = "Explicit profile-appropriate verification record"
    if profile.verification_mode == "check":
        latest = None
        verification_action = "reused"
        verification_reason = "Current successful check covers this task and project state"
        if not run_verification or not (no_cache or resume):
            try:
                latest = validated_check_report(root, state)
            except TaskStateError as exc:
                if not run_verification:
                    raise
                verification_reason = str(exc)
        else:
            verification_reason = "Explicit verification rerun requested"
        if latest is None:
            code, verification_output = _run_development_verification(
                root, no_cache=no_cache, resume=resume
            )
            verification_performed = True
            verification_action = "executed"
            if code != 0:
                detail = f": {verification_output}" if verification_output else ""
                raise TaskStateError(f"development verification failed with exit code {code}{detail}")
            latest = validated_check_report(root, state)
    else:
        latest = _require_fresh_success(state, latest_verification(root), "verification record")

    task = complete_task(root, summary=summary)
    return {
        "format": "agent-devtools-work-complete",
        "formatVersion": 1,
        "profile": profile.profile_id,
        "verificationMode": profile.verification_mode,
        "verificationPerformed": verification_performed,
        "verificationAction": verification_action,
        "verificationReason": verification_reason,
        "verificationOutput": verification_output,
        "knowledgeValidation": knowledge,
        "semanticCloseout": semantic,
        "latestVerification": latest,
        "task": task,
    }
