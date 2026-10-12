from __future__ import annotations

import argparse
import io
import subprocess
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_devtools.check import cli as check_cli
from agent_devtools.check.changes import git_changed_files
from agent_devtools.profiles import ProfileError, load_profile
from agent_devtools.check.freshness import validated_check_report
from .knowledge import remember as remember_knowledge, validate_knowledge
from .semantic_closeout import require_semantic_closeout, semantic_checkpoint
from .state import TaskStateError, complete_task, load_task_state, task_alignment_ready
from .verification import latest_verification, run_verification_command

_PASS_STATUSES = {"pass", "passed", "success", "ok"}


def _git_head_commit_epoch(root: Path) -> int | None:
    try:
        completed = subprocess.run(
            ["git", "show", "-s", "--format=%ct", "HEAD"],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip())
    except ValueError:
        return None


def _task_activity_epoch(state: dict[str, Any]) -> float | None:
    values = [
        str(state.get("lastEnteredAtUtc") or "").strip(),
        str(state.get("updatedAtUtc") or "").strip(),
    ]
    epochs: list[float] = []
    for value in values:
        if not value:
            continue
        try:
            epochs.append(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            continue
    return max(epochs) if epochs else None


def _require_current_task(root: Path, state: dict[str, Any], *, development: bool) -> None:
    if not development:
        return
    changed = git_changed_files(root)
    if changed is None or changed:
        return
    head_epoch = _git_head_commit_epoch(root)
    task_epoch = _task_activity_epoch(state)
    if head_epoch is None or task_epoch is None or task_epoch >= head_epoch:
        return
    goal = str(state.get("goal") or "").strip()
    raise TaskStateError(
        f"active task may be stale: {goal!r}; the canonical working tree is clean "
        "and current HEAD is newer than this task's last explicit entry/update. "
        "Next: run `agent begin` to explicitly resume and confirm this task, then retry "
        "`agent work complete`; if the goal is no longer current, start or replace the correct task instead."
    )


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

def _promote_required_semantics(root: Path) -> tuple[dict[str, Any], list[str]]:
    report = semantic_checkpoint(root)
    promoted: list[str] = []
    for item in report["requiredPromotions"]:
        record = remember_knowledge(root, event_id=str(item["eventId"]))
        promoted.append(str(record["id"]))
    if promoted:
        report = semantic_checkpoint(root)
    if report["requiredPromotions"]:
        missing = ", ".join(
            f"{item['kind']}:{item['subject']}" for item in report["requiredPromotions"][:5]
        )
        raise TaskStateError(
            "cannot complete work because required durable semantics remain after automatic promotion: "
            + missing
        )
    return report, promoted


def _executed_verification_covers_affected(item: dict[str, Any] | None) -> bool:
    if not item or str(item.get("status") or "").lower() not in _PASS_STATUSES:
        return False
    if str(item.get("sourceFreshness") or "") != "current":
        return False
    relevance = item.get("relevance") if isinstance(item.get("relevance"), dict) else {}
    suite = str(relevance.get("suite") or "")
    selected = [str(value) for value in relevance.get("selectedSuites", [])]
    return (
        relevance.get("status") == "relevant"
        and bool(suite)
        and selected == [suite]
        and not bool(relevance.get("fallbackFull"))
    )


def complete_work(
    root: Path,
    *,
    summary: str | None = None,
    run_verification: bool = True,
    no_cache: bool = False,
    resume: bool = False,
    verification_command: list[str] | tuple[str, ...] | None = None,
    verification_label: str = "completion-command",
    verification_timeout: float | None = None,
    verification_output_limit: int = 1600,
) -> dict[str, Any]:
    state = _require_ready_task(root)
    knowledge = validate_knowledge(root)
    if not knowledge.get("ok"):
        raise TaskStateError(
            "cannot complete work while durable knowledge has conflicts or dangling supersedes"
        )

    adaptive = bool(verification_command)
    semantic_promotions: list[str] = []
    executed: dict[str, Any] | None = None

    try:
        profile = load_profile(root)
    except ProfileError as exc:
        raise TaskStateError(str(exc)) from exc

    _require_current_task(root, state, development=bool(profile.development))

    if not adaptive:
        try:
            semantic = require_semantic_closeout(root)
        except TaskStateError as exc:
            raise TaskStateError(
                str(exc)
                + "\nNext: run `agent cognition checkpoint` to review durable candidates; "
                  "if the REQUIRED candidates are correct, run `agent cognition checkpoint --promote-required`, "
                  "then retry `agent work complete`."
            ) from exc

    if adaptive and profile.profile_id == "research":
        raise TaskStateError(
            "work complete --verify does not replace structured research verification; "
            "run `agent verify research`, then `agent work complete`"
        )

    verification_performed = False
    verification_output = ""
    verification_action = "recorded"
    verification_reason = "Explicit profile-appropriate verification record"

    if adaptive:
        executed_raw = run_verification_command(
            root,
            command=verification_command or (),
            label=verification_label,
            timeout=verification_timeout,
            output_limit=verification_output_limit,
        )
        verification_performed = True
        verification_action = "executed-command"
        verification_reason = "Explicit completion command executed by Agent DevTools"
        if str(executed_raw.get("status") or "").lower() not in _PASS_STATUSES:
            raise TaskStateError(
                f"completion verification command failed with exit code {executed_raw.get('exitCode')}"
            )
        executed = latest_verification(root) or executed_raw
        semantic, semantic_promotions = _promote_required_semantics(root)
        knowledge = validate_knowledge(root)
        if not knowledge.get("ok"):
            raise TaskStateError(
                "cannot complete work while durable knowledge has conflicts or dangling supersedes"
            )

    if profile.verification_mode == "check":
        latest = executed if _executed_verification_covers_affected(executed) else None
        if latest is not None:
            verification_action = "executed-command"
            verification_reason = "Executed command exactly covers the full affected verification plan"
        else:
            if adaptive:
                verification_reason = (
                    "Explicit command did not prove full affected-plan coverage; "
                    "project verification is still required"
                )
            else:
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
        candidate = executed if adaptive else latest_verification(root)
        try:
            latest = _require_fresh_success(state, candidate, "verification record")
        except TaskStateError as exc:
            if profile.profile_id == "research":
                raise TaskStateError(
                    str(exc)
                    + "\nNext: run `agent verify research` for the guided review; "
                      "record the resulting verification, then retry `agent work complete`."
                ) from exc
            raise TaskStateError(
                str(exc)
                + "\nNext: record profile-appropriate verification with `agent verify`, "
                  "then retry `agent work complete`."
            ) from exc

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
        "semanticPromotions": semantic_promotions,
        "latestVerification": latest,
        "task": task,
    }
