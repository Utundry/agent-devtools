from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from agent_devtools.check import cli as check_cli
from agent_devtools.profiles import ProfileError, load_profile
from .brief import _latest_run
from .knowledge import validate_knowledge
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
    args = argparse.Namespace(
        profile="affected", base=None, changed=[], config=None,
        no_cache=no_cache, resume=resume, max_chunks=None,
        time_slice_seconds=None, json_output=False,
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
    try:
        profile = load_profile(root)
    except ProfileError as exc:
        raise TaskStateError(str(exc)) from exc

    verification_performed = False
    verification_output = ""
    if profile.verification_mode == "check":
        if run_verification:
            code, verification_output = _run_development_verification(
                root, no_cache=no_cache, resume=resume
            )
            verification_performed = True
            if code != 0:
                detail = f": {verification_output}" if verification_output else ""
                raise TaskStateError(f"development verification failed with exit code {code}{detail}")
        latest = _require_fresh_success(state, _latest_run(root), "Agent DevTools check run")
    else:
        latest = _require_fresh_success(state, latest_verification(root), "verification record")

    task = complete_task(root, summary=summary)
    return {
        "format": "agent-devtools-work-complete",
        "formatVersion": 1,
        "profile": profile.profile_id,
        "verificationMode": profile.verification_mode,
        "verificationPerformed": verification_performed,
        "verificationOutput": verification_output,
        "knowledgeValidation": knowledge,
        "latestVerification": latest,
        "task": task,
    }
