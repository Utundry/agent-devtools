from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from agent_devtools import __version__
from agent_devtools.core.hashing import sha256_file, stable_fingerprint
from agent_devtools.core.io import atomic_json_write
from agent_devtools.core.workspace import default_work_root

from .brief import build_brief, git_state
from .state import load_task_state, task_state_path

CHECKPOINT_FORMAT = "agent-devtools-checkpoint"
CHECKPOINT_VERSION = 1


class CheckpointError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_changed(root: Path) -> tuple[list[str], list[str]] | None:
    probe = _run_git(root, ["rev-parse", "--verify", "HEAD"])
    if probe is None or probe.returncode != 0:
        return None
    proc = _run_git(root, ["diff", "--name-status", "--no-renames", "-z", "HEAD"])
    cached = _run_git(root, ["diff", "--cached", "--name-status", "--no-renames", "-z", "HEAD"])
    untracked = _run_git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    changed: set[str] = set()
    deleted: set[str] = set()
    for item in (proc, cached):
        if item is None or item.returncode != 0:
            continue
        parts = item.stdout.split(b"\0")
        index = 0
        while index + 1 < len(parts):
            status_code = parts[index].decode("utf-8", errors="surrogateescape").strip()
            path = parts[index + 1].decode("utf-8", errors="surrogateescape").replace("\\", "/")
            while path.startswith("./"):
                path = path[2:]
            index += 2
            if not status_code or not path:
                continue
            if status_code[0] == "D":
                deleted.add(path)
                changed.discard(path)
            else:
                changed.add(path)
                deleted.discard(path)
    if untracked is not None and untracked.returncode == 0:
        for raw in untracked.stdout.split(b"\0"):
            if raw:
                value = raw.decode("utf-8", errors="surrogateescape").replace("\\", "/")
                while value.startswith("./"):
                    value = value[2:]
                changed.add(value)
    return sorted(changed), sorted(deleted)


def _safe_rel(raw: str) -> str:
    value = raw.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise CheckpointError(f"unsafe checkpoint path: {raw!r}")
    if value.startswith(".agent-cache/") or value.startswith(".agent-work/"):
        raise CheckpointError(f"runtime/cache path cannot be checkpointed: {value}")
    return value




def _safe_target(root: Path, rel: str) -> Path:
    root_resolved = root.resolve()
    target = (root_resolved / rel).resolve(strict=False)
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise CheckpointError(f"checkpoint path escapes project through symlink: {rel}") from exc
    return target



def _source_manifest_hashes(root: Path) -> tuple[dict[str, str], str | None]:
    manifest = root / "MANIFEST.sha256"
    if not manifest.is_file():
        return {}, None
    try:
        raw = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}, None
    hashes: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            digest, rel = line.split("  ", 1)
        except ValueError:
            return {}, None
        if len(digest) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in digest):
            return {}, None
        try:
            rel = _safe_rel(rel)
        except CheckpointError:
            return {}, None
        hashes[rel] = digest.lower()
    return hashes, sha256_file(manifest)

def _git_blob(root: Path, path: str) -> bytes | None:
    proc = _run_git(root, ["show", f"HEAD:{path}"])
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_write_bytes(zf: zipfile.ZipFile, name: str, data: bytes, *, mode: int = 0o644) -> None:
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | mode) << 16
    zf.writestr(info, data)


def _manifest_text(entries: dict[str, str]) -> bytes:
    return "".join(f"{digest}  {name}\n" for name, digest in sorted(entries.items())).encode("utf-8")


def _default_checkpoint_path(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return default_work_root(root) / "checkpoints" / f"checkpoint-{stamp}.agent-checkpoint.zip"


def create_checkpoint(
    root: Path,
    *,
    out: Path | None = None,
    include: Iterable[str] = (),
    max_bytes: int = 100 * 1024 * 1024,
) -> dict[str, Any]:
    root = root.resolve()
    git = git_state(root)
    git_changes = _git_changed(root)
    source_manifest_hashes, source_manifest_sha = _source_manifest_hashes(root)
    explicit = [_safe_rel(item) for item in include if str(item).strip()]
    if git_changes is None:
        changed = sorted(set(explicit + ((load_task_state(root) or {}).get("changedFiles") or [])))
        deleted: list[str] = []
        if not changed and load_task_state(root) is None:
            raise CheckpointError("Git unavailable and no explicit/task changed files are known")
    else:
        changed, deleted = git_changes
        changed = sorted(set(changed + explicit))
    changed = [_safe_rel(item) for item in changed]
    deleted = [_safe_rel(item) for item in deleted]

    files: list[dict[str, Any]] = []
    payloads: dict[str, bytes] = {}
    total_bytes = 0
    for rel in changed:
        path = _safe_target(root, rel)
        if not path.is_file():
            if rel not in deleted:
                deleted.append(rel)
            continue
        data = path.read_bytes()
        total_bytes += len(data)
        if total_bytes > max_bytes:
            raise CheckpointError(f"checkpoint payload exceeds max bytes ({max_bytes})")
        archive_name = f"files/{rel}"
        payloads[archive_name] = data
        base = _git_blob(root, rel) if git.get("available") else None
        base_sha = _sha(base) if base is not None else source_manifest_hashes.get(rel)
        files.append({
            "path": rel,
            "bytes": len(data),
            "sha256": _sha(data),
            "baseSha256": base_sha,
            "mode": stat.S_IMODE(path.stat().st_mode),
        })

    deleted_rows: list[dict[str, Any]] = []
    for rel in sorted(set(deleted)):
        base = _git_blob(root, rel) if git.get("available") else None
        base_sha = _sha(base) if base is not None else source_manifest_hashes.get(rel)
        deleted_rows.append({"path": rel, "baseSha256": base_sha})

    task = load_task_state(root)
    brief = build_brief(root, mode="resume", budget=900, include_context=True)
    checkpoint = {
        "format": CHECKPOINT_FORMAT,
        "formatVersion": CHECKPOINT_VERSION,
        "toolVersion": __version__,
        "createdAtUtc": _utc_now(),
        "project": {"rootName": root.name},
        "base": {
            "gitAvailable": bool(git.get("available")),
            "gitHead": git.get("head"),
            "branch": git.get("branch"),
            "sourceManifestSha256": source_manifest_sha,
        },
        "files": files,
        "deleted": deleted_rows,
        "taskIncluded": task is not None,
        "briefIncluded": True,
        "workingStateFingerprint": brief.get("workingStateFingerprint"),
    }
    payloads["checkpoint.json"] = (json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    payloads["brief.json"] = (json.dumps(brief, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if task is not None:
        payloads["task.json"] = task_state_path(root).read_bytes()
    entries = {name: _sha(data) for name, data in payloads.items()}
    payloads["MANIFEST.sha256"] = _manifest_text(entries)

    out_path = (out or _default_checkpoint_path(root)).expanduser()
    if not out_path.is_absolute():
        out_path = (root / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp = out_path.with_suffix(out_path.suffix + ".tmp")
    with zipfile.ZipFile(temp, "w") as zf:
        for name in sorted(payloads):
            mode = 0o644
            if name.startswith("files/"):
                rel = name[len("files/"):]
                row = next((item for item in files if item["path"] == rel), None)
                if row is not None:
                    mode = int(row.get("mode") or 0o644)
            _zip_write_bytes(zf, name, payloads[name], mode=mode)
    os.replace(temp, out_path)
    return {
        "status": "pass",
        "path": str(out_path),
        "sha256": sha256_file(out_path),
        "files": len(files),
        "deleted": len(deleted_rows),
        "bytes": total_bytes,
        "gitHead": git.get("head"),
        "workingStateFingerprint": checkpoint["workingStateFingerprint"],
    }


def _read_checkpoint(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise CheckpointError(f"checkpoint not found: {path}")
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            if "MANIFEST.sha256" not in names or "checkpoint.json" not in names:
                raise CheckpointError("checkpoint is missing MANIFEST.sha256 or checkpoint.json")
            payloads = {name: zf.read(name) for name in names if not name.endswith("/")}
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise CheckpointError(f"cannot read checkpoint: {exc}") from exc
    manifest = payloads["MANIFEST.sha256"].decode("utf-8", errors="strict")
    expected: dict[str, str] = {}
    for raw in manifest.splitlines():
        if not raw.strip():
            continue
        try:
            digest, name = raw.split("  ", 1)
        except ValueError as exc:
            raise CheckpointError("malformed checkpoint manifest") from exc
        expected[name] = digest
    for name, digest in expected.items():
        data = payloads.get(name)
        if data is None:
            raise CheckpointError(f"checkpoint manifest references missing entry: {name}")
        if _sha(data) != digest:
            raise CheckpointError(f"checkpoint payload hash mismatch: {name}")
    extra = sorted(set(payloads) - set(expected) - {"MANIFEST.sha256"})
    if extra:
        raise CheckpointError("checkpoint contains unmanifested payload: " + ", ".join(extra[:5]))
    try:
        meta = json.loads(payloads["checkpoint.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"invalid checkpoint metadata: {exc}") from exc
    if not isinstance(meta, dict) or meta.get("format") != CHECKPOINT_FORMAT or int(meta.get("formatVersion") or 0) != CHECKPOINT_VERSION:
        raise CheckpointError("unsupported checkpoint format/version")
    return meta, payloads


def inspect_checkpoint(path: Path) -> dict[str, Any]:
    meta, _payloads = _read_checkpoint(path)
    return {
        "status": "pass",
        "path": str(path.expanduser().resolve()),
        "sha256": sha256_file(path.expanduser().resolve()),
        "createdAtUtc": meta.get("createdAtUtc"),
        "toolVersion": meta.get("toolVersion"),
        "gitHead": ((meta.get("base") or {}).get("gitHead") if isinstance(meta.get("base"), dict) else None),
        "files": len(meta.get("files") or []),
        "deleted": len(meta.get("deleted") or []),
        "taskIncluded": bool(meta.get("taskIncluded")),
        "workingStateFingerprint": meta.get("workingStateFingerprint"),
    }


def _current_sha(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def restore_checkpoint(root: Path, path: Path, *, force: bool = False) -> dict[str, Any]:
    root = root.resolve()
    meta, payloads = _read_checkpoint(path)
    base = meta.get("base") if isinstance(meta.get("base"), dict) else {}
    expected_head = str(base.get("gitHead") or "")
    current_git = git_state(root)
    if expected_head and current_git.get("available") and str(current_git.get("head") or "") != expected_head and not force:
        raise CheckpointError(
            f"checkpoint base HEAD {expected_head[:12]} does not match current HEAD {str(current_git.get('head') or '')[:12]}; use --force to override"
        )
    expected_manifest_sha = str(base.get("sourceManifestSha256") or "")
    current_manifest = root / "MANIFEST.sha256"
    if expected_manifest_sha and current_manifest.is_file() and sha256_file(current_manifest) != expected_manifest_sha and not force:
        raise CheckpointError("checkpoint source-package manifest does not match current source base; use --force to override")

    conflicts: list[str] = []
    writes: list[tuple[Path, bytes, int]] = []
    deletes: list[Path] = []
    for row in meta.get("files") or []:
        if not isinstance(row, dict):
            continue
        rel = _safe_rel(str(row.get("path") or ""))
        target = _safe_target(root, rel)
        target_data = payloads.get(f"files/{rel}")
        if target_data is None:
            raise CheckpointError(f"checkpoint is missing file payload: {rel}")
        current = _current_sha(target)
        target_sha = str(row.get("sha256") or "")
        base_sha = str(row.get("baseSha256") or "") or None
        safe = current is None or current == target_sha or (base_sha is not None and current == base_sha)
        if not safe and not force:
            conflicts.append(rel)
            continue
        writes.append((target, target_data, int(row.get("mode") or 0o644)))
    for row in meta.get("deleted") or []:
        if not isinstance(row, dict):
            continue
        rel = _safe_rel(str(row.get("path") or ""))
        target = _safe_target(root, rel)
        current = _current_sha(target)
        base_sha = str(row.get("baseSha256") or "") or None
        safe = current is None or (base_sha is not None and current == base_sha)
        if not safe and not force:
            conflicts.append(rel)
            continue
        deletes.append(target)
    task_payload = payloads.get("task.json")
    task_target = task_state_path(root)
    if task_payload is not None and task_target.is_file():
        current_task = task_target.read_bytes()
        if current_task != task_payload and not force:
            conflicts.append(str(task_target.relative_to(root) if task_target.is_relative_to(root) else task_target))
    if conflicts:
        raise CheckpointError("restore would overwrite divergent local state: " + ", ".join(sorted(conflicts)[:20]))

    for target, data, mode in writes:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".agent-restore.tmp")
        temp.write_bytes(data)
        os.chmod(temp, mode)
        os.replace(temp, target)
    for target in deletes:
        if target.exists():
            if target.is_dir():
                raise CheckpointError(f"refusing to delete directory from file checkpoint: {target}")
            target.unlink()
    task_restored = False
    if task_payload is not None:
        task_target.parent.mkdir(parents=True, exist_ok=True)
        temp = task_target.with_suffix(task_target.suffix + ".tmp")
        temp.write_bytes(task_payload)
        os.replace(temp, task_target)
        task_restored = True
    return {
        "status": "pass",
        "checkpoint": str(path.expanduser().resolve()),
        "restoredFiles": len(writes),
        "deletedFiles": len(deletes),
        "taskRestored": task_restored,
        "workingStateFingerprint": meta.get("workingStateFingerprint"),
    }
