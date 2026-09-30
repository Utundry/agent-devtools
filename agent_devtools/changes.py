from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from agent_devtools.check.config import CheckConfig, CheckConfigError, load_check_config
from agent_devtools.core.pathmatch import matches_any

CHANGESET_FORMAT = "agent-devtools-change-set"
CHANGESET_VERSION = 2
LOCAL_MARKS_FORMAT = "agent-devtools-workspace-local-changes"
LOCAL_MARKS_VERSION = 1
LOCAL_MARKS_FILE = "agent-devtools-local-changes.json"


class ChangeSetError(RuntimeError):
    pass


def _run_git(root: Path, args: list[str], *, binary: bool = False) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChangeSetError(f"cannot run git {' '.join(args)}: {exc}") from exc


def _normalize(path: str) -> str:
    value = path.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value



def _local_marks_path(root: Path) -> Path:
    root = root.resolve()
    probe = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        raise ChangeSetError("workspace-local marks require a Git working tree")
    top = _run_git(root, ["rev-parse", "--show-toplevel"])
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root:
        raise ChangeSetError("workspace-local marks require the Git working-tree root")
    git_path = _run_git(root, ["rev-parse", "--git-path", LOCAL_MARKS_FILE])
    if git_path.returncode != 0 or not git_path.stdout.strip():
        raise ChangeSetError("cannot resolve Git workspace-local mark storage path")
    relative = Path(git_path.stdout.strip())
    return (relative if relative.is_absolute() else root / relative).resolve()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_rel(raw: str) -> str:
    rel = _normalize(str(raw).strip())
    candidate = Path(rel)
    if not rel or candidate.is_absolute() or ".." in candidate.parts:
        raise ChangeSetError(f"workspace-local path must stay inside the project root: {raw!r}")
    return rel


def load_workspace_local_marks(root: Path) -> dict[str, dict[str, str]]:
    path = _local_marks_path(root.resolve())
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChangeSetError(f"workspace-local mark file is unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("format") != LOCAL_MARKS_FORMAT or raw.get("formatVersion") != LOCAL_MARKS_VERSION:
        raise ChangeSetError("workspace-local mark file has an unsupported format")
    items = raw.get("paths", {})
    if not isinstance(items, dict):
        raise ChangeSetError("workspace-local mark file paths must be an object")
    result: dict[str, dict[str, str]] = {}
    for raw_rel, value in items.items():
        rel = _safe_rel(str(raw_rel))
        if not isinstance(value, dict):
            raise ChangeSetError(f"workspace-local mark for {rel} must be an object")
        sha = str(value.get("sha256") or "").strip().lower()
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            raise ChangeSetError(f"workspace-local mark for {rel} has invalid sha256")
        result[rel] = {"sha256": sha, "reason": str(value.get("reason") or "").strip()}
    return result


def _write_workspace_local_marks(root: Path, marks: dict[str, dict[str, str]]) -> None:
    path = _local_marks_path(root.resolve())
    payload = {
        "format": LOCAL_MARKS_FORMAT,
        "formatVersion": LOCAL_MARKS_VERSION,
        "paths": {key: marks[key] for key in sorted(marks)},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def mark_workspace_local(root: Path, paths: list[str] | tuple[str, ...], *, reason: str = "") -> dict[str, Any]:
    root = root.resolve()
    _local_marks_path(root)
    marks = load_workspace_local_marks(root)
    marked: list[str] = []
    for raw in paths:
        rel = _safe_rel(raw)
        path = root / rel
        if not path.is_file():
            raise ChangeSetError(f"workspace-local path is not a regular file: {rel}")
        marks[rel] = {"sha256": _sha256_file(path), "reason": str(reason).strip()}
        marked.append(rel)
    _write_workspace_local_marks(root, marks)
    return {"format": LOCAL_MARKS_FORMAT, "formatVersion": LOCAL_MARKS_VERSION, "marked": sorted(marked), "path": str(_local_marks_path(root))}


def unmark_workspace_local(root: Path, paths: list[str] | tuple[str, ...]) -> dict[str, Any]:
    root = root.resolve()
    marks = load_workspace_local_marks(root)
    removed: list[str] = []
    for raw in paths:
        rel = _safe_rel(raw)
        if rel in marks:
            marks.pop(rel)
            removed.append(rel)
    _write_workspace_local_marks(root, marks)
    return {"format": LOCAL_MARKS_FORMAT, "formatVersion": LOCAL_MARKS_VERSION, "removed": sorted(removed), "path": str(_local_marks_path(root))}


def workspace_local_status(root: Path) -> dict[str, Any]:
    root = root.resolve()
    marks = load_workspace_local_marks(root)
    rows: list[dict[str, Any]] = []
    for rel in sorted(marks):
        path = root / rel
        current = _sha256_file(path) if path.is_file() else None
        expected = marks[rel]["sha256"]
        rows.append({
            "path": rel,
            "sha256": expected,
            "reason": marks[rel].get("reason", ""),
            "matches": current == expected,
            "currentSha256": current,
        })
    return {
        "format": LOCAL_MARKS_FORMAT,
        "formatVersion": LOCAL_MARKS_VERSION,
        "path": str(_local_marks_path(root)),
        "marks": rows,
        "count": len(rows),
        "matching": sum(1 for row in rows if row["matches"]),
    }


def _workspace_local_match(root: Path, rel: str, marks: dict[str, dict[str, str]]) -> bool:
    item = marks.get(rel)
    if item is None:
        return False
    path = root / rel
    if not path.is_file():
        return False
    try:
        return _sha256_file(path) == item["sha256"]
    except OSError:
        return False

def _canonical_path(config: CheckConfig, rel: str) -> bool:
    if config.ignore and matches_any(rel, config.ignore):
        return False
    if config.replay.source_exclude and matches_any(rel, config.replay.source_exclude):
        return False
    return matches_any(rel, config.replay.source_include)


def discover_changes(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    try:
        _local_marks_path(root)
    except ChangeSetError:
        return {
            "format": CHANGESET_FORMAT,
            "formatVersion": CHANGESET_VERSION,
            "available": False,
            "reason": "git-unavailable",
            "entries": [],
            "canonicalChangedFiles": [],
            "canonicalUntrackedFiles": [],
            "knowledgeChangedFiles": [],
        }
    try:
        config = load_check_config(root, config_path)
        local_marks = load_workspace_local_marks(root)
    except CheckConfigError as exc:
        raise ChangeSetError(str(exc)) from exc

    head = _run_git(root, ["rev-parse", "--verify", "HEAD"])
    if head.returncode != 0:
        return {
            "format": CHANGESET_FORMAT,
            "formatVersion": CHANGESET_VERSION,
            "available": False,
            "reason": "head-unavailable",
            "entries": [],
            "canonicalChangedFiles": [],
            "canonicalUntrackedFiles": [],
            "knowledgeChangedFiles": [],
        }

    tracked = _run_git(root, ["diff", "--name-status", "--no-renames", "-z", "HEAD"])
    untracked = _run_git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    if tracked.returncode != 0 or untracked.returncode != 0:
        raise ChangeSetError("git change discovery failed")

    rows: dict[str, dict[str, Any]] = {}
    parts = tracked.stdout.split("\0")
    index = 0
    status_names = {"A": "added", "D": "deleted", "M": "modified", "T": "type-changed"}
    while index + 1 < len(parts):
        status = parts[index].strip()
        rel = _normalize(parts[index + 1])
        index += 2
        if not status or not rel:
            continue
        code = status[0]
        source_canonical = _canonical_path(config, rel)
        workspace_local = _workspace_local_match(root, rel, local_marks)
        canonical = source_canonical and not workspace_local
        rows[rel] = {
            "path": rel,
            "status": status_names.get(code, code),
            "canonical": canonical,
            "sourceCanonical": source_canonical,
            "workspaceLocal": workspace_local,
            "patchIncluded": canonical,
            "durableKnowledge": rel.startswith(".agent-knowledge/") and rel.endswith(".json"),
        }

    for raw in untracked.stdout.split("\0"):
        rel = _normalize(raw.strip())
        if not rel:
            continue
        source_canonical = _canonical_path(config, rel)
        workspace_local = _workspace_local_match(root, rel, local_marks)
        canonical = source_canonical and not workspace_local
        rows.setdefault(rel, {
            "path": rel,
            "status": "untracked",
            "canonical": canonical,
            "sourceCanonical": source_canonical,
            "workspaceLocal": workspace_local,
            "patchIncluded": canonical,
            "durableKnowledge": rel.startswith(".agent-knowledge/") and rel.endswith(".json"),
        })

    entries = [rows[path] for path in sorted(rows)]
    canonical = [row["path"] for row in entries if row["canonical"]]
    canonical_untracked = [row["path"] for row in entries if row["canonical"] and row["status"] == "untracked"]
    workspace_local = [row["path"] for row in entries if row.get("workspaceLocal")]
    changed_paths = {row["path"] for row in entries}
    mark_mismatches = [
        rel for rel in sorted(local_marks)
        if rel in changed_paths and not _workspace_local_match(root, rel, local_marks)
    ]
    knowledge = [row["path"] for row in entries if row["durableKnowledge"]]
    return {
        "format": CHANGESET_FORMAT,
        "formatVersion": CHANGESET_VERSION,
        "available": True,
        "head": head.stdout.strip(),
        "entries": entries,
        "canonicalChangedFiles": canonical,
        "canonicalUntrackedFiles": canonical_untracked,
        "workspaceLocalChangedFiles": workspace_local,
        "workspaceLocalMarkMismatches": mark_mismatches,
        "knowledgeChangedFiles": knowledge,
        "canonicalUntrackedCount": len(canonical_untracked),
        "workspaceLocalChangedCount": len(workspace_local),
        "knowledgeChangedCount": len(knowledge),
    }


def _empty_file_patch(rel: str) -> bytes:
    # git diff --no-index emits no bytes for an empty new file. Emit the minimal
    # creation patch explicitly so exact project patches remain complete.
    return (
        f"diff --git a/{rel} b/{rel}\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
    ).encode("utf-8")


def build_patch(root: Path, config_path: Path | None = None) -> tuple[bytes, dict[str, Any]]:
    root = root.resolve()
    report = discover_changes(root, config_path)
    if not report.get("available"):
        raise ChangeSetError(f"cannot build project patch: {report.get('reason')}")

    canonical_tracked = [
        row["path"]
        for row in report["entries"]
        if row["canonical"] and row["status"] != "untracked"
    ]
    chunks: list[bytes] = []
    if canonical_tracked:
        tracked = _run_git(root, ["diff", "--binary", "HEAD", "--", *canonical_tracked], binary=True)
        if tracked.returncode != 0:
            raise ChangeSetError("git diff failed while building canonical project patch")
        chunks.append(tracked.stdout)

    for rel in report["canonicalUntrackedFiles"]:
        path = root / rel
        if not path.is_file():
            raise ChangeSetError(f"canonical untracked path is not a regular file: {rel}")
        if path.stat().st_size == 0:
            chunks.append(_empty_file_patch(rel))
            continue
        proc = _run_git(root, ["diff", "--no-index", "--binary", "--", "/dev/null", rel], binary=True)
        if proc.returncode not in (0, 1):
            raise ChangeSetError(f"cannot include untracked canonical file in patch: {rel}")
        if proc.stdout:
            chunks.append(proc.stdout)
    patch = b"".join(chunk if not chunk or chunk.endswith(b"\n") else chunk + b"\n" for chunk in chunks)
    report = dict(report)
    report["patchBytes"] = len(patch)
    report["patchFiles"] = list(report["canonicalChangedFiles"])
    return patch, report
