from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

COMMAND_OUTPUT_TAIL_BYTES = 512 * 1024
PROCESS_TERMINATE_GRACE_SECONDS = 3.0


class ProcessExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    stage: str
    returncode: int
    duration_seconds: float
    log_path: Path
    output_tail: str
    timed_out: bool
    timeout_reason: str | None = None


def safe_stage_name(stage: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", stage).strip("-") or "stage"


def _read_tail(path: Path, limit: int = COMMAND_OUTPUT_TAIL_BYTES) -> bytes:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            return handle.read(limit)
    except OSError:
        return b""


class ManagedProcessRunner:
    """Run one command with direct-to-disk logs and bounded post-run diagnostics.

    stdout/stderr go straight to the log file instead of through an in-process pipe.
    This keeps memory bounded, avoids reader-thread/pipe lifetime races, and ensures
    descendants cannot keep the parent verifier waiting merely by inheriting a pipe.
    """

    def __init__(self, *, root: Path, log_dir: Path, default_timeout_seconds: float = 600.0) -> None:
        self.root = root.resolve()
        self.log_dir = log_dir.resolve()
        self.default_timeout_seconds = max(0.05, float(default_timeout_seconds))
        self.log_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=PROCESS_TERMINATE_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=PROCESS_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass

    def run(
        self,
        stage: str,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
        idle_timeout_seconds: float | None = None,
        on_process_started: Callable[[int, list[str]], None] | None = None,
    ) -> ProcessResult:
        timeout = max(0.001, float(timeout_seconds or self.default_timeout_seconds))
        idle_timeout = None if idle_timeout_seconds is None else max(0.001, float(idle_timeout_seconds))
        started = time.monotonic()
        log_path = self.log_dir / f"{safe_stage_name(stage)}.log"
        timed_out = False
        timeout_reason: str | None = None
        returncode = 127
        proc: subprocess.Popen[bytes] | None = None

        try:
            command_list = list(command)
            with log_path.open("wb") as log:
                kwargs: dict[str, object] = {
                    "cwd": str((cwd or self.root).resolve()),
                    "env": dict(env) if env is not None else None,
                    "stdout": log,
                    "stderr": subprocess.STDOUT,
                    "text": False,
                }
                if os.name == "posix":
                    kwargs["start_new_session"] = True
                proc = subprocess.Popen(command_list, **kwargs)  # type: ignore[arg-type]
                if on_process_started is not None:
                    on_process_started(proc.pid, command_list)
                last_activity = time.monotonic()
                last_size = 0
                while True:
                    polled = proc.poll()
                    if polled is not None:
                        returncode = int(polled)
                        break
                    now = time.monotonic()
                    try:
                        log.flush()
                        size = log_path.stat().st_size
                    except OSError:
                        size = last_size
                    if size != last_size:
                        last_size = size
                        last_activity = now
                    if now - started >= timeout:
                        timed_out = True
                        timeout_reason = "hard"
                        self.terminate_process_tree(proc)
                        returncode = int(proc.returncode if proc.returncode is not None else 124)
                        break
                    if idle_timeout is not None and now - last_activity >= idle_timeout:
                        timed_out = True
                        timeout_reason = "idle"
                        self.terminate_process_tree(proc)
                        returncode = int(proc.returncode if proc.returncode is not None else 124)
                        break
                    interval = 0.01 if now - started < 0.1 else 0.1
                    remaining = min(interval, timeout - (now - started))
                    if idle_timeout is not None:
                        remaining = min(remaining, idle_timeout - (now - last_activity))
                    try:
                        proc.wait(timeout=max(0.001, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                log.flush()
        except OSError as exc:
            data = f"{type(exc).__name__}: {exc}\n".encode("utf-8", errors="replace")
            log_path.write_bytes(data)
            returncode = 127
        finally:
            if proc is not None and proc.poll() is None:
                self.terminate_process_tree(proc)

        if timed_out:
            returncode = 124
            detail = (
                f"idle for {idle_timeout:.1f}s"
                if timeout_reason == "idle" and idle_timeout is not None
                else f"exceeded hard limit {timeout:.1f}s"
            )
            marker = f"\nAGENT_DEVTOOLS_TIMEOUT: stage {stage} {detail}\n".encode()
            with log_path.open("ab") as log:
                log.write(marker)

        tail = _read_tail(log_path)
        return ProcessResult(
            stage=stage,
            returncode=returncode,
            duration_seconds=time.monotonic() - started,
            log_path=log_path,
            output_tail=tail.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            timeout_reason=timeout_reason,
        )
