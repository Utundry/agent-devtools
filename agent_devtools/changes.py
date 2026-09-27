from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agent_devtools.check.config import CheckConfig, CheckConfigError, load_check_config
from agent_devtools.core.pathmatch import matches_any

CHANGESET_FORMAT = "agent-devtools-change-set"
CHANGESET_VERSION = 1


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


def _canonical_path(config: CheckConfig, rel: str) -> bool:
    if config.ignore and matches_any(rel, config.ignore):
        return False
    if config.replay.source_exclude and matches_any(rel, config.replay.source_exclude):
        return False
    return matches_any(rel, config.replay.source_include)


def discover_changes(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    if not (root / ".git").exists():
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
        canonical = _canonical_path(config, rel)
        rows[rel] = {
            "path": rel,
            "status": status_names.get(code, code),
            "canonical": canonical,
            "patchIncluded": canonical,
            "durableKnowledge": rel.startswith(".agent-knowledge/") and rel.endswith(".json"),
        }

    for raw in untracked.stdout.split("\0"):
        rel = _normalize(raw.strip())
        if not rel:
            continue
        canonical = _canonical_path(config, rel)
        rows.setdefault(rel, {
            "path": rel,
            "status": "untracked",
            "canonical": canonical,
            "patchIncluded": canonical,
            "durableKnowledge": rel.startswith(".agent-knowledge/") and rel.endswith(".json"),
        })

    entries = [rows[path] for path in sorted(rows)]
    canonical = [row["path"] for row in entries if row["canonical"]]
    canonical_untracked = [row["path"] for row in entries if row["canonical"] and row["status"] == "untracked"]
    knowledge = [row["path"] for row in entries if row["durableKnowledge"]]
    return {
        "format": CHANGESET_FORMAT,
        "formatVersion": CHANGESET_VERSION,
        "available": True,
        "head": head.stdout.strip(),
        "entries": entries,
        "canonicalChangedFiles": canonical,
        "canonicalUntrackedFiles": canonical_untracked,
        "knowledgeChangedFiles": knowledge,
        "canonicalUntrackedCount": len(canonical_untracked),
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

    tracked = _run_git(root, ["diff", "--binary", "HEAD", "--"], binary=True)
    if tracked.returncode != 0:
        raise ChangeSetError("git diff failed while building project patch")
    chunks: list[bytes] = [tracked.stdout]

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
