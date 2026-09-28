from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import atomic_json_write

WORKSPACE_FORMAT = "agent-devtools-run"
WORKSPACE_VERSION = 1


class WorkspaceError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    if os.name == "posix":
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.is_file():
            try:
                raw = stat_path.read_text(encoding="utf-8", errors="replace")
                close = raw.rfind(")")
                fields = raw[close + 1:].strip().split() if close >= 0 else []
                if fields and fields[0] == "Z":
                    return False
            except OSError:
                pass
    return True


def default_work_root(root: Path) -> Path:
    override = os.environ.get("AGENT_DEVTOOLS_WORK_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    candidate = root.resolve() / ".agent-work"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:12]
        fallback = Path(tempfile.gettempdir()) / "agent-devtools" / key
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def prune_runs(work_root: Path, *, keep_runs: int = 20) -> None:
    runs_root = work_root / "runs"
    if not runs_root.is_dir():
        return
    runs = sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in runs[keep_runs:]:
        shutil.rmtree(old, ignore_errors=True)


def _lock_owner(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return 0


def recover_stale_run_state(root: Path) -> dict[str, Any]:
    work_root = default_work_root(root)
    lock_path = work_root / "run.lock"
    active_path = work_root / "active.json"
    active_removed = False
    lock_removed = False
    active_pid = 0
    active_live = False

    if active_path.is_file():
        try:
            payload = json.loads(active_path.read_text(encoding="utf-8"))
            active_pid = int(payload.get("pid") or 0) if isinstance(payload, dict) else 0
        except Exception:
            active_pid = 0
        active_live = bool(active_pid and pid_alive(active_pid))
        if not active_live:
            active_path.unlink(missing_ok=True)
            active_removed = True

    if lock_path.is_file():
        owner = _lock_owner(lock_path)
        if not active_live and (owner <= 0 or not pid_alive(owner)):
            lock_path.unlink(missing_ok=True)
            lock_removed = True

    return {
        "activeRemoved": active_removed,
        "lockRemoved": lock_removed,
        "activePid": active_pid or None,
    }


class RunWorkspace:
    def __init__(self, root: Path, mode: str) -> None:
        self.root = root.resolve()
        self.mode = mode
        self.work_root = default_work_root(self.root)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.work_root / "run.lock"
        self.active_path = self.work_root / "active.json"
        self._acquire_lock()
        prune_runs(self.work_root)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = f"{os.getpid():x}-{time.time_ns():x}"
        self.run_dir = self.work_root / "runs" / f"{stamp}-{mode}-{suffix}"
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.started = time.monotonic()
        self.report: dict[str, Any] = {
            "format": WORKSPACE_FORMAT,
            "formatVersion": WORKSPACE_VERSION,
            "status": "running",
            "mode": mode,
            "pid": os.getpid(),
            "projectRoot": str(self.root),
            "runDirectory": str(self.run_dir),
            "startedAtUtc": utc_now(),
            "currentStage": None,
            "activeProcess": None,
            "checks": {},
        }
        self.persist()

    def _acquire_lock(self) -> None:
        recover_stale_run_state(self.root)
        active = active_status(self.root)
        if active is not None and int(active.get("pid") or 0) != os.getpid():
            raise WorkspaceError(
                f"another Agent DevTools run is active (pid {int(active.get('pid') or 0)})"
            )
        for _ in range(2):
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(str(os.getpid()) + "\n")
                return
            except FileExistsError:
                owner = _lock_owner(self.lock_path)
                if owner and pid_alive(owner):
                    raise WorkspaceError(f"another Agent DevTools run is active (pid {owner})")
                self.lock_path.unlink(missing_ok=True)
        raise WorkspaceError("could not acquire Agent DevTools run lock")

    def persist(self) -> None:
        if self.report.get("status") == "running":
            self.report["elapsedSeconds"] = round(time.monotonic() - self.started, 3)
        atomic_json_write(self.run_dir / "report.json", self.report)
        if self.report.get("status") == "running":
            atomic_json_write(self.active_path, {
                "format": WORKSPACE_FORMAT,
                "formatVersion": WORKSPACE_VERSION,
                "status": "running",
                "mode": self.mode,
                "pid": os.getpid(),
                "startedAtUtc": self.report.get("startedAtUtc"),
                "currentStage": self.report.get("currentStage"),
                "currentChunk": (((self.report.get("checks") or {}).get(self.report.get("currentStage")) or {}).get("currentChunk") if self.report.get("currentStage") else None),
                "chunkProgress": (((self.report.get("checks") or {}).get(self.report.get("currentStage")) or {}).get("chunkProgress") if self.report.get("currentStage") else None),
                "activeProcess": self.report.get("activeProcess"),
                "elapsedSeconds": self.report.get("elapsedSeconds"),
                "runDirectory": str(self.run_dir),
                "report": str(self.run_dir / "report.json"),
            })

    def stage_started(self, stage: str) -> None:
        self.report["currentStage"] = stage
        self.report["checks"].setdefault(stage, {}).update({"status": "running", "startedAtUtc": utc_now()})
        self.persist()

    def process_started(self, pid: int, command: list[str]) -> None:
        self.report["activeProcess"] = {"pid": pid, "command": command[:6]}
        self.persist()

    def chunk_started(self, stage: str, chunk: str, *, index: int, total: int) -> None:
        check = self.report["checks"].setdefault(stage, {})
        check.setdefault("chunks", {})
        check["currentChunk"] = chunk
        check["chunkProgress"] = {"index": index, "total": total}
        check["chunks"].setdefault(chunk, {}).update({"status": "running", "startedAtUtc": utc_now()})
        self.persist()

    def chunk_finished(self, stage: str, chunk: str, payload: dict[str, Any]) -> None:
        check = self.report["checks"].setdefault(stage, {})
        check.setdefault("chunks", {})
        check["chunks"].setdefault(chunk, {}).update(payload)
        check["currentChunk"] = None
        self.report["activeProcess"] = None
        self.persist()

    def stage_finished(self, stage: str, payload: dict[str, Any]) -> None:
        self.report["checks"].setdefault(stage, {}).update(payload)
        self.report["currentStage"] = None
        self.report["activeProcess"] = None
        self.persist()

    def finalize(self, status: str, *, message: str | None = None) -> None:
        self.report["status"] = status
        self.report["completedAtUtc"] = utc_now()
        self.report["durationSeconds"] = round(time.monotonic() - self.started, 3)
        self.report["currentStage"] = None
        self.report["activeProcess"] = None
        if message:
            self.report["message"] = message
        atomic_json_write(self.run_dir / "report.json", self.report)
        try:
            if self.active_path.is_file():
                payload = json.loads(self.active_path.read_text(encoding="utf-8"))
                if int(payload.get("pid") or -1) == os.getpid():
                    self.active_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            if self.lock_path.is_file() and int(self.lock_path.read_text(encoding="utf-8").strip() or "0") == os.getpid():
                self.lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def active_status(root: Path) -> dict[str, Any] | None:
    recover_stale_run_state(root)
    path = default_work_root(root) / "active.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    pid = int(payload.get("pid") or 0)
    if not pid_alive(pid):
        path.unlink(missing_ok=True)
        return None
    return payload


def cancel_active(root: Path) -> bool:
    active = active_status(root)
    if not active:
        return False
    proc_info = active.get("activeProcess") if isinstance(active.get("activeProcess"), dict) else {}
    target_pid = int((proc_info or {}).get("pid") or active.get("pid") or 0)
    if target_pid <= 0:
        return False
    try:
        if os.name == "posix" and proc_info:
            os.killpg(target_pid, signal.SIGTERM)
        else:
            os.kill(target_pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False
