from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from . import __version__
from .profiles import ProfileError, load_profile
from .work.knowledge import KnowledgeError, effective_lifecycle_statuses, load_records
from .work.state import TaskStateError, validate_task_state


def _work_root(root: Path) -> Path:
    override = os.environ.get("AGENT_DEVTOOLS_WORK_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return root.resolve() / ".agent-work"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _current_task(root: Path) -> dict[str, Any] | None:
    payload = _read_json(_work_root(root) / "task.json")
    if payload is None:
        return None
    try:
        return validate_task_state(payload)
    except TaskStateError:
        return None


def _journal_stats(root: Path, current_task_id: str = "") -> dict[str, Any]:
    path = _work_root(root) / "semantic-journal.sqlite3"
    empty = {"available": False, "sessionsSeen": 1 if current_task_id else 0, "events": 0, "byKind": {}}
    if not path.is_file():
        return empty
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_events'"
            ).fetchone()
            if not table:
                return empty
            sessions = int(conn.execute(
                "SELECT COUNT(DISTINCT task_id) FROM semantic_events"
            ).fetchone()[0] or 0)
            if current_task_id:
                present = conn.execute(
                    "SELECT 1 FROM semantic_events WHERE task_id = ? LIMIT 1",
                    (current_task_id,),
                ).fetchone()
                if not present:
                    sessions += 1
            rows = conn.execute(
                "SELECT kind, COUNT(*) FROM semantic_events GROUP BY kind ORDER BY kind"
            ).fetchall()
    except (sqlite3.Error, OSError):
        return empty
    by_kind = {str(kind): int(count) for kind, count in rows}
    return {
        "available": True,
        "sessionsSeen": sessions,
        "events": sum(by_kind.values()),
        "byKind": by_kind,
    }


def _knowledge_stats(root: Path) -> dict[str, Any]:
    try:
        records = load_records(root)
        lifecycle = effective_lifecycle_statuses(records)
    except (KnowledgeError, OSError):
        return {"available": False, "records": 0, "byLifecycle": {}}
    counts: dict[str, int] = {}
    for record in records:
        status = str(lifecycle.get(record["id"], "active"))
        counts[status] = counts.get(status, 0) + 1
    return {
        "available": True,
        "records": len(records),
        "byLifecycle": dict(sorted(counts.items())),
    }


def _verification_stats(root: Path) -> dict[str, Any]:
    payload = _read_json(_work_root(root) / "verification.json")
    records = payload.get("records", []) if isinstance(payload, dict) else []
    if not isinstance(records, list):
        records = []
    counts = {"pass": 0, "warn": 0, "fail": 0}
    latest = None
    for raw in records:
        if not isinstance(raw, dict):
            continue
        assessment = str(raw.get("assessmentStatus") or raw.get("status") or "").lower()
        if assessment in counts:
            counts[assessment] += 1
        latest = {
            "label": raw.get("label"),
            "status": assessment or None,
            "completedAtUtc": raw.get("completedAtUtc"),
            "warningChecks": list(raw.get("warningChecks") or []),
        }
    return {
        "records": sum(counts.values()),
        "pass": counts["pass"],
        "warn": counts["warn"],
        "fail": counts["fail"],
        "latest": latest,
    }


def _context_stats(root: Path) -> dict[str, Any]:
    path = _work_root(root) / "context-usage.sqlite3"
    empty = {
        "available": False,
        "projections": 0,
        "repeatProjections": 0,
        "selectedTokens": 0,
        "averageSelectedTokens": None,
        "repeatRate": None,
    }
    if not path.is_file():
        return empty
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='context_projections'"
            ).fetchone()
            if not table:
                return empty
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(repeated), 0), "
                "COALESCE(SUM(primary_tokens + related_tokens), 0) "
                "FROM context_projections"
            ).fetchone()
    except (sqlite3.Error, OSError):
        return empty
    projections, repeats, tokens = (int(row[0]), int(row[1]), int(row[2]))
    return {
        "available": True,
        "projections": projections,
        "repeatProjections": repeats,
        "selectedTokens": tokens,
        "averageSelectedTokens": round(tokens / projections, 1) if projections else None,
        "repeatRate": round(repeats / projections, 4) if projections else None,
    }


def _tree_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _fallback_files(root: Path) -> list[str]:
    ignored = {".git", ".agent-work", ".agent-cache", ".agent-updates", "build", "__pycache__"}
    result: list[str] = []
    for path in root.rglob("*"):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part in ignored for part in rel.parts):
            continue
        try:
            if path.is_file() and not path.is_symlink():
                result.append(rel.as_posix())
        except OSError:
            continue
    return sorted(result)


def _repository_stats(root: Path) -> dict[str, Any]:
    tracked_raw = _git(root, "ls-files", "-z")
    tracked = [item for item in (tracked_raw or "").split("\0") if item] if tracked_raw is not None else _fallback_files(root)
    branch_raw = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    status_raw = _git(root, "status", "--porcelain")
    test_cases = 0
    for rel in tracked:
        if not (rel.startswith("tests/") and rel.endswith(".py")):
            continue
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        test_cases += len(re.findall(r"(?m)^\s*def\s+test_[A-Za-z0-9_]*\s*\(", text))
    return {
        "gitAvailable": tracked_raw is not None,
        "branch": branch_raw.strip() if branch_raw else None,
        "workingTree": (
            "dirty" if status_raw and status_raw.strip()
            else "clean" if status_raw is not None
            else None
        ),
        "files": len(tracked),
        "pythonFiles": sum(1 for item in tracked if item.endswith(".py")),
        "testCases": test_cases,
    }


def _update_stats(root: Path) -> dict[str, Any]:
    base = root / ".agent-updates"
    incoming = base / "incoming"
    applied = base / "applied"
    runs = base / "runs"
    return {
        "incoming": len(list(incoming.glob("*.json"))) if incoming.is_dir() else 0,
        "appliedScenarios": len(list(applied.glob("*.json"))) if applied.is_dir() else 0,
        "releaseRuns": len([item for item in runs.iterdir() if item.is_dir()]) if runs.is_dir() else 0,
    }


def build_overview(root: Path) -> dict[str, Any]:
    root = root.resolve()
    current = _current_task(root)
    task_id = str((current or {}).get("taskId") or "")
    try:
        profile = load_profile(root).profile_id
    except ProfileError:
        profile = None
    try:
        project_version = (root / "VERSION").read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeError):
        project_version = None

    cognition = _journal_stats(root, task_id)
    knowledge = _knowledge_stats(root)
    verification = _verification_stats(root)
    context = _context_stats(root)
    updates = _update_stats(root)
    repository = _repository_stats(root)
    work_root = _work_root(root)

    attention = {
        "openQuestions": len((current or {}).get("openQuestions", [])),
        "blockers": len((current or {}).get("blockers", [])),
        "verificationWarnings": verification["warn"],
        "verificationFailures": verification["fail"],
        "incomingUpdates": updates["incoming"],
    }

    return {
        "format": "agent-devtools-project-overview",
        "formatVersion": 1,
        "toolVersion": __version__,
        "projectRoot": str(root),
        "projectVersion": project_version,
        "profile": profile,
        "work": {
            "sessionsSeen": cognition["sessionsSeen"],
            "currentStatus": (current or {}).get("status"),
            "currentGoal": (current or {}).get("goal"),
            "currentTaskId": task_id or None,
        },
        "cognition": cognition,
        "knowledge": knowledge,
        "verification": verification,
        "context": context,
        "updates": updates,
        "repository": repository,
        "storage": {
            "workBytes": _tree_size(work_root),
            "knowledgeBytes": _tree_size(root / ".agent-knowledge"),
        },
        "attention": attention,
        "readOnly": True,
    }


def _human_bytes(value: int) -> str:
    amount = float(max(0, int(value)))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024.0 or unit == "GiB":
            return f"{int(amount)} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} GiB"


def render_overview(payload: dict[str, Any]) -> str:
    repo = payload["repository"]
    work = payload["work"]
    cognition = payload["cognition"]
    knowledge = payload["knowledge"]
    verification = payload["verification"]
    context = payload["context"]
    updates = payload["updates"]
    storage = payload["storage"]
    attention = payload["attention"]
    kinds = cognition.get("byKind", {})
    lifecycle = knowledge.get("byLifecycle", {})

    lines = [
        "AGENT DEVTOOLS · PROJECT OVERVIEW",
        "",
        "Project",
        f"  Tool version:      {payload['toolVersion']}",
        f"  Project version:   {payload['projectVersion'] or 'n/a'}",
        f"  Profile:           {payload['profile'] or 'unknown'}",
        f"  Git:               {(repo.get('branch') or 'n/a')} · {(repo.get('workingTree') or 'n/a')}",
        "",
        "Work",
        f"  Sessions seen:     {work['sessionsSeen']}",
        f"  Current:           {work['currentStatus'] or 'none'}",
        f"  Goal:              {work['currentGoal'] or '—'}",
        "",
        "Cognition",
        f"  Semantic events:   {cognition['events']}",
        f"  Findings:          {kinds.get('finding', 0)}",
        f"  Decisions:         {kinds.get('decision', 0)}",
        f"  Requirements:      {kinds.get('requirement', 0)}",
        f"  Assumptions:       {kinds.get('assumption', 0)}",
        f"  Questions:         {kinds.get('question', 0)}",
        f"  Evidence:          {kinds.get('evidence', 0)}",
        "",
        "Knowledge",
        f"  Records:           {knowledge['records']}",
        f"  Active:            {lifecycle.get('active', 0)}",
        f"  Superseded:        {lifecycle.get('superseded', 0)}",
        f"  Historical:        {lifecycle.get('historical', 0)}",
        f"  Rejected:          {lifecycle.get('rejected', 0)}",
        "",
        "Verification",
        f"  Records:           {verification['records']}",
        f"  PASS/WARN/FAIL:    {verification['pass']}/{verification['warn']}/{verification['fail']}",
        "",
        "Context",
        f"  Projections:       {context['projections']}",
        f"  Selected tokens:   {context['selectedTokens']}",
        f"  Avg/projection:    {context['averageSelectedTokens'] if context['averageSelectedTokens'] is not None else 'n/a'}",
        f"  Repeated:          {context['repeatProjections']}",
        "",
        "Updates",
        f"  Incoming:          {updates['incoming']}",
        f"  Applied scenarios: {updates['appliedScenarios']}",
        f"  Release runs:      {updates['releaseRuns']}",
        "",
        "Repository",
        f"  Files:             {repo['files']}",
        f"  Python files:      {repo['pythonFiles']}",
        f"  Test cases:        {repo['testCases']}",
        f"  Work state:        {_human_bytes(storage['workBytes'])}",
        f"  Durable knowledge: {_human_bytes(storage['knowledgeBytes'])}",
        "",
        "Attention",
        f"  Open questions:    {attention['openQuestions']}",
        f"  Blockers:          {attention['blockers']}",
        f"  Verification WARN: {attention['verificationWarnings']}",
        f"  Verification FAIL: {attention['verificationFailures']}",
        f"  Incoming updates:  {attention['incomingUpdates']}",
    ]
    return "\n".join(lines)


def configure_parser(parser) -> None:
    sub = parser.add_subparsers(dest="project_command", required=True)
    overview = sub.add_parser("overview", help="show a compact human statistical overview of the project")
    overview.add_argument("--json", action="store_true", dest="json_output")


def main(root: Path, args) -> int:
    if args.project_command != "overview":
        return 2
    payload = build_overview(root)
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_overview(payload))
    return 0
