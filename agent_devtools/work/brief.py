from __future__ import annotations

import json
import sqlite3
import os
import subprocess
from pathlib import Path
from typing import Any

from agent_devtools import __version__
from agent_devtools.context.affected import build_affected_briefing
from agent_devtools.context.config import ContextConfigError, load_context_config
from agent_devtools.context.index import ContextIndexError
from agent_devtools.context.semantic_diff import compare_semantic_states
from agent_devtools.context.search import query_context
from agent_devtools.profiles import ProfileError, load_profile
from agent_devtools.core.hashing import stable_fingerprint
from agent_devtools.core.workspace import active_status, default_work_root, pid_alive

from .state import TaskStateError, load_task_state
from .verification import VerificationError, latest_verification

BRIEF_FORMAT = "agent-devtools-work-brief"
BRIEF_VERSION = 1


class BriefError(RuntimeError):
    pass


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def git_state(root: Path) -> dict[str, Any]:
    head_proc = _run_git(root, ["rev-parse", "--verify", "HEAD"])
    if head_proc is None or head_proc.returncode != 0:
        return {"available": False, "head": None, "branch": None, "changedFiles": []}
    head = head_proc.stdout.decode("utf-8", errors="replace").strip()
    branch_proc = _run_git(root, ["branch", "--show-current"])
    branch = branch_proc.stdout.decode("utf-8", errors="replace").strip() if branch_proc and branch_proc.returncode == 0 else ""
    tracked_proc = _run_git(root, ["diff", "--name-status", "--no-renames", "-z", "HEAD"])
    staged_proc = _run_git(root, ["diff", "--cached", "--name-status", "--no-renames", "-z", "HEAD"])
    untracked_proc = _run_git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    changed: dict[str, str] = {}
    for proc in (tracked_proc, staged_proc):
        if proc is None or proc.returncode != 0:
            continue
        parts = proc.stdout.split(b"\0")
        index = 0
        while index + 1 < len(parts):
            status = parts[index].decode("utf-8", errors="surrogateescape").strip()
            path = parts[index + 1].decode("utf-8", errors="surrogateescape")
            index += 2
            if not status or not path:
                continue
            code = status[0]
            normalized = path.replace("\\", "/")
            while normalized.startswith("./"):
                normalized = normalized[2:]
            changed[normalized] = {"A": "added", "D": "deleted", "M": "modified", "T": "type-changed"}.get(code, code)
    if untracked_proc is not None and untracked_proc.returncode == 0:
        for raw in untracked_proc.stdout.split(b"\0"):
            if raw:
                path = raw.decode("utf-8", errors="surrogateescape").replace("\\", "/")
                while path.startswith("./"):
                    path = path[2:]
                changed.setdefault(path, "untracked")
    rows = [{"path": path, "status": changed[path]} for path in sorted(changed)]
    return {"available": True, "head": head, "branch": branch or None, "changedFiles": rows}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _latest_run(root: Path) -> dict[str, Any] | None:
    runs_root = default_work_root(root) / "runs"
    if not runs_root.is_dir():
        return None
    candidates = sorted(
        (path for path in runs_root.iterdir() if path.is_dir() and (path / "report.json").is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    report_path = candidates[0] / "report.json"
    report = _read_json(report_path)
    if report is None:
        return None
    active = active_status(root)
    interrupted = False
    if str(report.get("status") or "") == "running":
        same_active = bool(active and str(active.get("runDirectory") or "") == str(candidates[0]))
        interrupted = not same_active
        if same_active:
            pid = int(report.get("pid") or 0)
            interrupted = not pid_alive(pid)
    checks = []
    for name, value in (report.get("checks") or {}).items():
        if not isinstance(value, dict):
            continue
        checks.append({
            "id": str(name),
            "status": str(value.get("status") or "unknown"),
            "durationSeconds": value.get("durationSeconds"),
            "certifiedEvidence": value.get("certifiedEvidence"),
            "diagnostics": value.get("diagnostics") or [],
        })
    return {
        "status": str(report.get("status") or "unknown"),
        "mode": report.get("mode"),
        "startedAtUtc": report.get("startedAtUtc"),
        "completedAtUtc": report.get("completedAtUtc"),
        "currentStage": report.get("currentStage"),
        "activeProcess": report.get("activeProcess"),
        "message": report.get("message"),
        "runDirectory": str(candidates[0]),
        "report": str(report_path),
        "interrupted": interrupted,
        "checks": checks,
        "certification": report.get("certification"),
    }


def _context(root: Path, changed: tuple[str, ...], budget: int, before_root: Path | None = None) -> dict[str, Any] | None:
    if not changed:
        return None
    try:
        config = load_context_config(root)
        semantic_identities: tuple[str, ...] = ()
        semantic_diff = None
        if before_root is not None:
            resolved_before = before_root.expanduser().resolve()
            if not resolved_before.is_dir():
                raise ContextConfigError(f"brief --before is not a directory: {resolved_before}")
            before_config = load_context_config(resolved_before)
            semantic_diff = compare_semantic_states(before_config, config, include_unchanged=False)
            semantic_identities = tuple(
                str(item["identity"]) for item in semantic_diff["changes"]
                if item.get("status") in {"added", "removed", "modified"}
            )
        briefing = build_affected_briefing(
            root, config, changed_files=changed, budget=budget, limit=8, semantic_identities=semantic_identities
        )
    except (ContextConfigError, ContextIndexError, OSError, ValueError, sqlite3.Error) as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "changedFiles": list(briefing.changed_files),
        "semanticIdentities": list(briefing.semantic_identities),
        "semanticDiffCounts": semantic_diff["counts"] if semantic_diff is not None else None,
        "initialImpact": list(briefing.initial_impact),
        "effectiveImpact": list(briefing.effective_impact),
        "selectedSuites": list(briefing.selected_suites),
        "selectedApplicationGroups": list(briefing.selected_application_groups),
        "fallbackFull": briefing.fallback_full,
        "fallbackReasons": list(briefing.fallback_reasons),
        "query": briefing.query,
        "budget": briefing.budget,
        "estimatedTokens": briefing.estimated_tokens,
        "omitted": briefing.omitted,
        "results": [
            {
                "id": item.chunk_id,
                "path": item.path,
                "kind": item.kind,
                "label": item.label,
                "startLine": item.start_line,
                "endLine": item.end_line,
                "sourceKind": item.source_kind,
                "why": list(item.why),
                "content": item.content,
            }
            for item in briefing.results
        ],
    }


def _generic_context(root: Path, task: dict[str, Any] | None, budget: int) -> dict[str, Any] | None:
    if not isinstance(task, dict):
        return None
    query_parts = [str(task.get("goal") or ""), *[str(x) for x in task.get("scope", [])], *[str(x) for x in task.get("requirements", [])], *[str(x) for x in task.get("openQuestions", [])], *[str(x) for x in task.get("decisions", [])], *[str(x) for x in task.get("findings", [])], *[str(x) for x in task.get("evidence", [])]]
    query = " ".join(part.strip() for part in query_parts if part.strip())
    if not query:
        return None
    try:
        config = load_context_config(root)
        report = query_context(config, query, limit=8, budget=budget, preferred_paths=task.get("changedFiles", []))
    except (ContextConfigError, ContextIndexError, OSError, ValueError, sqlite3.Error) as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "query": report.query,
        "budget": report.budget,
        "estimatedTokens": report.estimated_tokens,
        "omitted": report.omitted,
        "results": [
            {"id": item.chunk_id, "path": item.path, "kind": item.kind, "label": item.label, "startLine": item.start_line, "endLine": item.end_line, "sourceKind": item.source_kind, "why": list(item.why), "content": item.content}
            for item in report.results
        ],
    }


def build_brief(root: Path, *, mode: str = "brief", budget: int = 1400, include_context: bool = True, before_root: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    try:
        task = load_task_state(root)
    except TaskStateError as exc:
        task = {"error": str(exc)}
    try:
        profile = load_profile(root)
    except ProfileError as exc:
        profile = None
        profile_error = str(exc)
    else:
        profile_error = None
    git = git_state(root)
    git_changed = tuple(row["path"] for row in git.get("changedFiles", []) if isinstance(row, dict) and row.get("path"))
    task_changed = tuple(str(item) for item in ((task or {}).get("changedFiles") or []) if str(item).strip()) if isinstance(task, dict) else ()
    use_git_changes = bool(profile is None or profile.change_discovery == "git+task")
    changed = tuple(dict.fromkeys((*(git_changed if use_git_changes else ()), *task_changed)))
    latest_run = _latest_run(root)
    try:
        generic_verification = latest_verification(root)
    except VerificationError:
        generic_verification = None
    latest = latest_run if profile is None or profile.verification_mode == "check" else generic_verification
    if include_context:
        context = _context(root, changed, budget, before_root=before_root) if (profile is None or profile.development) else _generic_context(root, task if isinstance(task, dict) else None, budget)
    else:
        context = None
    recovery: dict[str, Any] = {
        "interruptedRunDetected": bool(latest_run and latest_run.get("interrupted")) if (profile is None or profile.development) else False,
        "nextStep": (task or {}).get("nextStep") if isinstance(task, dict) else None,
        "recommendedAction": None,
    }
    if recovery["interruptedRunDetected"]:
        stage = latest_run.get("currentStage") if latest_run else None
        recovery["recommendedAction"] = f"inspect current working tree, then resume from interrupted stage {stage!r}" if stage else "inspect current working tree and resume from task nextStep"
    elif isinstance(task, dict) and task.get("status") == "completed":
        recovery["recommendedAction"] = "task is completed; start a new work item when needed"
    elif changed:
        recovery["recommendedAction"] = "continue from the current project state; tracked work inputs changed"
    elif task:
        recovery["recommendedAction"] = "continue from task nextStep"
    else:
        recovery["recommendedAction"] = "no active task state detected"
    profile_payload = None if profile is None else {"id": profile.profile_id, "title": profile.title, "development": profile.development, "verificationMode": profile.verification_mode, "changeDiscovery": profile.change_discovery}
    payload = {
        "format": BRIEF_FORMAT,
        "formatVersion": BRIEF_VERSION,
        "mode": mode,
        "toolVersion": __version__,
        "projectRoot": str(root),
        "profile": profile_payload,
        "profileError": profile_error,
        "task": task,
        "git": git,
        "changedFiles": list(changed),
        "changeDiscovery": (
            "task" if profile is not None and not profile.development
            else ("git+task" if git_changed and task_changed else ("git" if git_changed else ("task" if task_changed else "none")))
        ),
        "latestRun": latest_run,
        "latestVerification": latest,
        "recovery": recovery,
        "context": context,
    }
    payload["workingStateFingerprint"] = stable_fingerprint({
        "profile": profile_payload,
        "task": task,
        "git": {"head": git.get("head"), "changedFiles": git.get("changedFiles") if use_git_changes else []},
        "changedFiles": list(changed),
        "latestVerification": latest,
    })
    return payload


def render_brief(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    mode = str(payload.get("mode") or "brief").upper()
    lines.append(f"AGENT {mode}")
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else None
    if profile:
        lines.append(f"Profile: {profile.get('id')} · verify={profile.get('verificationMode')}")
    task = payload.get("task") if isinstance(payload.get("task"), dict) else None
    if task and not task.get("error"):
        lines.append(f"Goal: {task.get('goal')}")
        if task.get("summary"):
            lines.append(f"Progress: {task.get('summary')}")
        if task.get("nextStep"):
            lines.append(f"Next: {task.get('nextStep')}")
        if task.get("constraints"):
            lines.append("Constraints: " + "; ".join(task["constraints"]))
        if task.get("definitionOfDone"):
            lines.append("Done when: " + "; ".join(task["definitionOfDone"]))
        if task.get("decisions"):
            lines.append("Decisions: " + "; ".join(task["decisions"]))
        if task.get("findings"):
            lines.append("Findings: " + "; ".join(task["findings"]))
        if task.get("requirements"):
            lines.append("Requirements: " + "; ".join(task["requirements"]))
        if task.get("openQuestions"):
            lines.append("Open questions: " + "; ".join(task["openQuestions"]))
        if task.get("assumptions"):
            lines.append("Assumptions: " + "; ".join(task["assumptions"]))
        if task.get("evidence"):
            lines.append("Evidence: " + "; ".join(task["evidence"]))
        if task.get("blockers"):
            lines.append("Blockers: " + "; ".join(task["blockers"]))
    elif task and task.get("error"):
        lines.append(f"Task state error: {task['error']}")
    else:
        lines.append("Goal: no local task state")

    recovery = payload.get("recovery") if isinstance(payload.get("recovery"), dict) else {}
    if recovery.get("interruptedRunDetected"):
        lines.append("Recovery: interrupted Agent DevTools run detected")
    if recovery.get("recommendedAction"):
        lines.append(f"Resume: {recovery['recommendedAction']}")

    git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
    development = bool(profile and profile.get("development"))
    if development:
        if git.get("available"):
            head = str(git.get("head") or "")[:12]
            branch = f" branch={git.get('branch')}" if git.get("branch") else ""
            lines.append(f"Base: git {head}{branch}")
            changes = git.get("changedFiles") or []
            lines.append(f"Working tree: {len(changes)} changed file(s)")
            for row in changes[:20]:
                lines.append(f"  {row.get('status')}: {row.get('path')}")
            if len(changes) > 20:
                lines.append(f"  … {len(changes) - 20} more")
        else:
            lines.append("Base: Git unavailable")
    else:
        lines.append(f"Workspace: {payload.get('projectRoot')}")

    latest = payload.get("latestRun") if isinstance(payload.get("latestRun"), dict) else None
    if latest:
        suffix = " · INTERRUPTED" if latest.get("interrupted") else ""
        lines.append(f"Latest check: {latest.get('mode')} {latest.get('status')}{suffix}")
        if latest.get("currentStage"):
            lines.append(f"  last stage: {latest.get('currentStage')}")
        for row in latest.get("checks") or []:
            lines.append(f"  {row.get('id')}: {row.get('status')}")
        if latest.get("message"):
            lines.append(f"  error: {latest.get('message')}")

    context = payload.get("context") if isinstance(payload.get("context"), dict) else None
    if context:
        if not context.get("available"):
            lines.append(f"Context: unavailable ({context.get('error')})")
        else:
            impacts = context.get("effectiveImpact") or []
            if impacts:
                lines.append("Impact: " + ", ".join(impacts))
            selected_suites = context.get("selectedSuites") or []
            if selected_suites:
                lines.append("Check plan: " + ", ".join(str(item) for item in selected_suites))
            semantic_count = len(context.get("semanticIdentities") or [])
            lines.append(
                f"Context: {len(context.get('results') or [])} chunk(s), "
                f"semantic={semantic_count}, ~{context.get('estimatedTokens', 0)}/{context.get('budget', 0)} tokens"
            )
            for item in context.get("results") or []:
                why = "; ".join(item.get("why") or [])
                lines.append(
                    f"  [{item.get('sourceKind')}] {item.get('id')} · {item.get('path')}:"
                    f"{item.get('startLine')}-{item.get('endLine')}"
                )
                if why:
                    lines.append(f"    why: {why}")
                content = str(item.get("content") or "").strip()
                if content:
                    for raw in content.splitlines():
                        lines.append("    " + raw)
    lines.append(f"State: {str(payload.get('workingStateFingerprint') or '')[:16]}")
    return "\n".join(lines)
