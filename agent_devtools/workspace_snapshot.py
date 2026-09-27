from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from agent_devtools import __version__
from agent_devtools.context.config import load_context_config
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


def _excluded(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    if rel in _EXCLUDE_NAMES:
        return True
    if any(rel.startswith(prefix) for prefix in _EXCLUDE_PREFIXES):
        return True
    name = PurePosixPath(rel).name
    if any(name.startswith(prefix) and name.endswith(".py") for prefix in _EXCLUDE_PREFIX_NAMES):
        return True
    return False


def _iter_artifacts(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if _excluded(rel):
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

    payloads: dict[str, bytes] = {}
    rows: list[dict[str, Any]] = []
    total = 0
    for rel, path in _iter_artifacts(root):
        data = path.read_bytes()
        total += len(data)
        if total > max_bytes:
            raise WorkspaceSnapshotError(f"workspace snapshot exceeds max bytes ({max_bytes})")
        name = f"artifacts/{rel}"
        payloads[name] = data
        rows.append({"path": rel, "bytes": len(data), "sha256": _sha(data), "mode": stat.S_IMODE(path.stat().st_mode)})

    task = load_task_state(root)
    if task is not None:
        payloads["state/task.json"] = task_state_path(root).read_bytes()
    verify = verification_path(root)
    if verify.is_file():
        payloads["state/verification.json"] = verify.read_bytes()

    corpus_hash = hashlib.sha256()
    for row in sorted(rows, key=lambda x: x["path"]):
        corpus_hash.update(row["path"].encode("utf-8")); corpus_hash.update(b"\0")
        corpus_hash.update(row["sha256"].encode("ascii")); corpus_hash.update(b"\n")

    metadata = {
        "format": SNAPSHOT_FORMAT,
        "formatVersion": SNAPSHOT_VERSION,
        "toolVersion": __version__,
        "createdAtUtc": _utc_now(),
        "workspace": {"name": root.name, "profile": profile.profile_id},
        "corpusFingerprint": corpus_hash.hexdigest(),
        "artifacts": rows,
        "state": {"taskIncluded": task is not None, "verificationIncluded": verify.is_file()},
        "excluded": [".git", "devtools/agent", ".agent-cache", ".agent-work raw state", ".agent-bootstrap-report.json", "bootstrap installers"],
    }
    payloads["snapshot.json"] = (json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    entries = {name: _sha(data) for name, data in payloads.items()}
    payloads["MANIFEST.sha256"] = _manifest(entries)

    target = (out or _default_path(root)).expanduser()
    if not target.is_absolute():
        target = (root / target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    with zipfile.ZipFile(temp, "w") as zf:
        for name in sorted(payloads):
            mode = 0o644
            if name.startswith("artifacts/"):
                rel = name[len("artifacts/"):]
                row = next((x for x in rows if x["path"] == rel), None)
                if row: mode = int(row["mode"])
            _zip_write(zf, name, payloads[name], mode)
    os.replace(temp, target)
    return {"status": "pass", "path": str(target), "sha256": _sha(target.read_bytes()), "profile": profile.profile_id, "artifacts": len(rows), "bytes": total, "corpusFingerprint": metadata["corpusFingerprint"]}


def _read_snapshot(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise WorkspaceSnapshotError(f"snapshot not found: {path}")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if "snapshot.json" not in names or "MANIFEST.sha256" not in names:
            raise WorkspaceSnapshotError("snapshot is missing metadata/manifest")
        manifest_lines = zf.read("MANIFEST.sha256").decode("utf-8").splitlines()
        expected: dict[str, str] = {}
        for line in manifest_lines:
            if not line.strip(): continue
            digest, name = line.split("  ", 1)
            expected[name] = digest
        payloads: dict[str, bytes] = {}
        for name, digest in expected.items():
            data = zf.read(name)
            if _sha(data) != digest:
                raise WorkspaceSnapshotError(f"snapshot payload hash mismatch: {name}")
            payloads[name] = data
        meta = json.loads(payloads["snapshot.json"].decode("utf-8"))
        if meta.get("format") != SNAPSHOT_FORMAT or int(meta.get("formatVersion") or 0) != SNAPSHOT_VERSION:
            raise WorkspaceSnapshotError("unsupported snapshot format/version")
        return meta, payloads


def inspect_snapshot(path: Path) -> dict[str, Any]:
    meta, _ = _read_snapshot(path)
    return {"status": "pass", "path": str(path.expanduser().resolve()), **meta}


def restore_snapshot(path: Path, target: Path, *, force: bool = False) -> dict[str, Any]:
    meta, payloads = _read_snapshot(path)
    target = target.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    conflicts: list[str] = []
    for name, data in payloads.items():
        if not name.startswith("artifacts/"): continue
        rel = _safe_rel(name[len("artifacts/"):])
        dest = (target / rel).resolve()
        try: dest.relative_to(target)
        except ValueError as exc: raise WorkspaceSnapshotError(f"snapshot path escapes target: {rel}") from exc
        if dest.is_file() and dest.read_bytes() != data and not force:
            conflicts.append(rel)
    if conflicts:
        raise WorkspaceSnapshotError("restore conflicts; rerun with --force only after review: " + ", ".join(conflicts[:20]))

    restored = 0
    for name, data in payloads.items():
        if not name.startswith("artifacts/"): continue
        rel = _safe_rel(name[len("artifacts/"):])
        dest = target / rel; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data); restored += 1
    state_root = target / ".agent-work"; state_root.mkdir(parents=True, exist_ok=True)
    if "state/task.json" in payloads:
        (state_root / "task.json").write_bytes(payloads["state/task.json"])
    if "state/verification.json" in payloads:
        (state_root / "verification.json").write_bytes(payloads["state/verification.json"])
    return {"status": "pass", "target": str(target), "profile": meta["workspace"]["profile"], "restoredArtifacts": restored, "taskRestored": "state/task.json" in payloads, "verificationRestored": "state/verification.json" in payloads, "corpusFingerprint": meta["corpusFingerprint"]}
