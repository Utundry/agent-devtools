from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from agent_devtools import __version__
from agent_devtools.core.archive import ArchiveSafetyError, verified_zip, read_metadata
from agent_devtools.core.hashing import sha256_file
from agent_devtools.core.files import iter_paths
from agent_devtools.core.pathmatch import matches_any
from agent_devtools.profiles import load_profile
from agent_devtools.work.state import load_task_state, task_state_path
from agent_devtools.work.verification import verification_path

SNAPSHOT_FORMAT = "agent-devtools-workspace-snapshot"
SNAPSHOT_VERSION = 1

_EXCLUDE_PREFIXES = (
    ".git/", ".agent-cache/", ".agent-work/", "devtools/agent/",
    ".agent-devtools-bootstrap-kit/", "node_modules/", "vendor/", "dist/", "build/", "coverage/",
)
_EXCLUDE_NAMES = {".agent-bootstrap-report.json"}
_EXCLUDE_PREFIX_NAMES = ("agent-devtools-bootstrap-",)

_DEFAULT_PRESERVE_EXCLUDE = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "credentials.json",
    "**/credentials.json",
    "service-account*.json",
    "**/service-account*.json",
    "id_rsa",
    "**/id_rsa",
    "id_ed25519",
    "**/id_ed25519",
    "*.key",
    "**/*.key",
    "*.p12",
    "**/*.p12",
    "*.pfx",
    "**/*.pfx",
)


class WorkspaceSnapshotError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_rel(raw: str) -> str:
    value = raw.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    p = PurePosixPath(value)
    if not value or p.is_absolute() or ".." in p.parts:
        raise WorkspaceSnapshotError(f"unsafe snapshot path: {raw!r}")
    return value


def _project_preserve_exclude(root: Path) -> tuple[str, ...]:
    config = root / "agent-tools.json"
    if not config.is_file():
        return ()
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceSnapshotError(f"cannot read agent-tools.json preservation settings: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkspaceSnapshotError("agent-tools.json root must be an object")
    preserve = raw.get("preserve") or {}
    if not isinstance(preserve, dict):
        raise WorkspaceSnapshotError("agent-tools.json preserve must be an object")
    value = preserve.get("exclude", [])
    if not isinstance(value, list):
        raise WorkspaceSnapshotError("agent-tools.json preserve.exclude must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise WorkspaceSnapshotError("agent-tools.json preserve.exclude must contain non-empty strings")
        pattern = item.strip().replace("\\", "/")
        candidate = PurePosixPath(pattern)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise WorkspaceSnapshotError(
                f"agent-tools.json preserve.exclude must stay inside the workspace: {item!r}"
            )
        if pattern not in result:
            result.append(pattern)
    return tuple(result)


def _excluded(rel: str, preserve_exclude: tuple[str, ...]) -> bool:
    rel = rel.replace("\\", "/")
    if rel in _EXCLUDE_NAMES:
        return True
    if any(rel.startswith(prefix) for prefix in _EXCLUDE_PREFIXES):
        return True
    name = PurePosixPath(rel).name
    if any(name.startswith(prefix) and name.endswith(".py") for prefix in _EXCLUDE_PREFIX_NAMES):
        return True
    return matches_any(rel, (*_DEFAULT_PRESERVE_EXCLUDE, *preserve_exclude))


def _iter_artifacts(root: Path, preserve_exclude: tuple[str, ...], skip: tuple[Path, ...] = ()):
    for path in iter_paths(root, exclude=(*[prefix.rstrip("/") + "/**" for prefix in _EXCLUDE_PREFIXES], *preserve_exclude)):
        if path.is_symlink() or path.resolve() in skip:
            continue
        rel = path.relative_to(root).as_posix()
        if _excluded(rel, preserve_exclude) or path.name.endswith((".agent-workspace-snapshot.zip", ".agent-handoff.zip", ".agent-checkpoint.zip")):
            continue
        yield rel, path


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | mode) << 16
    zf.writestr(info, data)


def _manifest(entries: dict[str, str]) -> bytes:
    return "".join(f"{digest}  {name}\n" for name, digest in sorted(entries.items())).encode("utf-8")


def _default_path(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / f"{root.name}-{stamp}.agent-workspace-snapshot.zip"


def create_snapshot(root: Path, *, out: Path | None = None, max_bytes: int = 250 * 1024 * 1024) -> dict[str, Any]:
    root = root.resolve()
    profile = load_profile(root)
    if profile.development:
        raise WorkspaceSnapshotError("workspace snapshot is intended for non-development profiles; use release/checkpoint flows for development")
    target = (out or _default_path(root)).expanduser()
    target = target.resolve() if target.is_absolute() else (root / target).resolve()
    temp = target.with_suffix(target.suffix + ".tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    rows, entries = [], {}
    total = 0
    preserve_exclude = _project_preserve_exclude(root)
    try:
        with zipfile.ZipFile(temp, "w") as archive:
            def add_file(path, name):
                nonlocal total
                size = path.stat().st_size
                if total + size > max_bytes:
                    raise WorkspaceSnapshotError(f"workspace snapshot exceeds max bytes ({max_bytes})")
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                mode = stat.S_IMODE(path.stat().st_mode)
                info.external_attr = (stat.S_IFREG | mode) << 16
                digest, actual = hashlib.sha256(), 0
                with path.open("rb") as source, archive.open(info, "w") as sink:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        actual += len(block)
                        if total + actual > max_bytes:
                            raise WorkspaceSnapshotError(f"workspace snapshot exceeds max bytes ({max_bytes})")
                        digest.update(block)
                        sink.write(block)
                total += actual
                entries[name] = digest.hexdigest()
                return actual, mode, digest.hexdigest()
            for rel, path in _iter_artifacts(root, preserve_exclude, (target, temp)):
                count, mode, digest = add_file(path, "artifacts/" + rel)
                rows.append({"path": rel, "bytes": count, "sha256": digest, "mode": mode})
            task = load_task_state(root)
            if task is not None:
                add_file(task_state_path(root), "state/task.json")
            verify = verification_path(root)
            if verify.is_file():
                add_file(verify, "state/verification.json")
            corpus = hashlib.sha256()
            for row in sorted(rows, key=lambda item: item["path"]):
                corpus.update(row["path"].encode()); corpus.update(b"\0")
                corpus.update(row["sha256"].encode()); corpus.update(b"\n")
            metadata = {"format": SNAPSHOT_FORMAT, "formatVersion": SNAPSHOT_VERSION,
                "toolVersion": __version__, "createdAtUtc": _utc_now(),
                "workspace": {"name": root.name, "profile": profile.profile_id},
                "corpusFingerprint": corpus.hexdigest(), "artifacts": rows,
                "state": {"taskIncluded": task is not None, "verificationIncluded": verify.is_file()},
                "excluded": ["runtime/cache", "snapshot/handoff/checkpoint outputs", "bootstrap installers"],
                "sensitiveDefaultsExcluded": True, "projectPreserveExclude": list(preserve_exclude)}
            data = (json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
            _zip_write(archive, "snapshot.json", data)
            entries["snapshot.json"] = _sha(data)
            _zip_write(archive, "MANIFEST.sha256", _manifest(entries))
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return {"status": "pass", "path": str(target), "sha256": sha256_file(target), "profile": profile.profile_id,
            "artifacts": len(rows), "bytes": sum(row["bytes"] for row in rows), "corpusFingerprint": metadata["corpusFingerprint"]}


def _metadata(archive, members):
    if "snapshot.json" not in members:
        raise WorkspaceSnapshotError("snapshot is missing metadata")
    try:
        meta = json.loads(read_metadata(archive, members["snapshot.json"]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceSnapshotError(f"invalid snapshot metadata: {exc}") from exc
    if not isinstance(meta, dict) or meta.get("format") != SNAPSHOT_FORMAT or int(meta.get("formatVersion") or 0) != SNAPSHOT_VERSION:
        raise WorkspaceSnapshotError("unsupported snapshot format/version")
    return meta


def inspect_snapshot(path: Path) -> dict[str, Any]:
    try:
        with verified_zip(path) as (archive, members, _expected):
            meta = _metadata(archive, members)
    except ArchiveSafetyError as exc:
        raise WorkspaceSnapshotError(str(exc)) from exc
    return {"status": "pass", "path": str(path.expanduser().resolve()), **meta}


def restore_snapshot(path: Path, target: Path, *, force: bool = False) -> dict[str, Any]:
    target = target.expanduser().resolve()
    try:
        with verified_zip(path) as (archive, members, expected):
            meta = _metadata(archive, members)
            conflicts, writes = [], []
            destinations = set()
            modes = {row["path"]: row.get("mode", 0o644) for row in meta.get("artifacts", [])}
            for name, info in members.items():
                if name.startswith("artifacts/"):
                    rel = _safe_rel(name[len("artifacts/"):])
                elif name in {"state/task.json", "state/verification.json"}:
                    rel = ".agent-work/" + name[len("state/"):]
                else:
                    continue
                dest = (target / rel).resolve()
                try:
                    dest.relative_to(target)
                except ValueError as exc:
                    raise WorkspaceSnapshotError(f"snapshot path escapes target: {rel}") from exc
                if dest in destinations:
                    raise WorkspaceSnapshotError(f"duplicate snapshot destination: {rel}")
                destinations.add(dest)
                if any(parent.exists() and not parent.is_dir() for parent in dest.parents if parent != target and target in parent.parents):
                    conflicts.append(rel)
                if dest.exists() and (not dest.is_file() or sha256_file(dest) != expected[name]) and not force:
                    conflicts.append(rel)
                writes.append((dest, info, modes.get(rel, 0o644)))
            if conflicts:
                raise WorkspaceSnapshotError("restore conflicts; rerun with --force only after review: " + ", ".join(conflicts[:20]))
            for dest, info, mode in writes:
                dest.parent.mkdir(parents=True, exist_ok=True)
                temp = dest.with_name(dest.name + ".agent-restore.tmp")
                try:
                    with archive.open(info) as source, temp.open("wb") as sink:
                        shutil.copyfileobj(source, sink, length=1024 * 1024)
                    os.chmod(temp, int(mode))
                    os.replace(temp, dest)
                finally:
                    temp.unlink(missing_ok=True)
            return {"status": "pass", "target": str(target), "profile": meta["workspace"]["profile"],
                    "restoredArtifacts": sum(info.filename.startswith("artifacts/") for _, info, _ in writes),
                    "taskRestored": "state/task.json" in members, "verificationRestored": "state/verification.json" in members,
                    "corpusFingerprint": meta["corpusFingerprint"]}
    except ArchiveSafetyError as exc:
        raise WorkspaceSnapshotError(str(exc)) from exc
