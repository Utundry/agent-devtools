from __future__ import annotations

import json
import os
import math
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__
from .core.io import atomic_json_write

HANDOFF_URL = "https://raw.githubusercontent.com/Utundry/agent-devtools/main/AGENT-START-HERE.md"
INSTALLER_URL = "https://raw.githubusercontent.com/Utundry/agent-devtools/v{version}/bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py"
_SAFE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?$")
_PINNED_RE = re.compile(
    r"https://raw\.githubusercontent\.com/Utundry/agent-devtools/v"
    r"([^/\s]+)/bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME\.py"
)
_MAX_DOWNLOAD = 8 * 1024 * 1024


class SelfUpdateError(RuntimeError):
    pass


def _validate_version(value: str) -> str:
    value = value.strip()
    if not _SAFE_VERSION.fullmatch(value):
        raise SelfUpdateError(f"invalid Agent DevTools release version: {value!r}")
    return value


def extract_pinned_release(handoff_text: str) -> tuple[str, str]:
    versions = sorted(set(_PINNED_RE.findall(handoff_text)))
    if len(versions) != 1:
        raise SelfUpdateError(
            "canonical handoff must contain exactly one unambiguous pinned bootstrap release; "
            f"found {versions!r}"
        )
    version = _validate_version(versions[0])
    return version, INSTALLER_URL.format(version=version)


def _fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"Agent-DevTools/{__version__} self-update"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(_MAX_DOWNLOAD + 1)
    except Exception as exc:
        raise SelfUpdateError(f"cannot download {url}: {exc}") from exc
    if len(data) > _MAX_DOWNLOAD:
        raise SelfUpdateError(f"download exceeds {_MAX_DOWNLOAD} bytes: {url}")
    return data


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    # Do not use PIPE here. The downloaded single-file installer launches an
    # inner bootstrap process, which may in turn launch validation children.
    # On Windows, inherited stdout/stderr pipe handles can keep communicate()
    # waiting for EOF after the direct child has already completed. Regular
    # temporary files preserve captured output without that descendant-handle
    # lifetime coupling.
    args = list(argv)
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        proc = subprocess.run(
            args,
            stdout=stdout_file,
            stderr=stderr_file,
            check=False,
        )
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", errors="replace")
        stderr = stderr_file.read().decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(
        args=proc.args,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _json_stdout(proc: subprocess.CompletedProcess[str], stage: str) -> dict[str, Any]:
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise SelfUpdateError(f"{stage} failed ({proc.returncode}): {detail}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SelfUpdateError(f"{stage} did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise SelfUpdateError(f"{stage} returned a non-object JSON payload")
    return payload


def _installed_runtime_version(root: Path) -> str | None:
    path = root / "devtools" / "agent" / "agent_devtools" / "__init__.py"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


_RUNTIME_REENTRY_ENV = "AGENT_DEVTOOLS_RUNTIME_REENTRY"


def redispatch_to_installed_runtime(
    root: Path,
    argv: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> int:
    """Run the current command once through the freshly installed runtime.

    Standard streams are inherited deliberately: this preserves JSON/stdout contracts
    and avoids the Windows descendant-pipe lifetime problem fixed by self-update.
    """
    root = root.resolve()
    agent = root / "devtools" / "agent" / "agent.py"
    if not agent.is_file():
        raise SelfUpdateError(f"fresh installed runtime entry point is missing: {agent}")
    env = dict(os.environ)
    if env.get(_RUNTIME_REENTRY_ENV) == "1":
        raise SelfUpdateError("runtime re-entry is already active")
    env[_RUNTIME_REENTRY_ENV] = "1"
    command = [sys.executable, str(agent), *(str(item) for item in argv)]
    try:
        completed = runner(command, cwd=root, env=env, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SelfUpdateError(f"cannot re-enter freshly installed runtime: {exc}") from exc
    return int(completed.returncode)


def self_update(
    root: Path,
    *,
    requested_version: str | None = None,
    check_only: bool = False,
    timeout: float = 30.0,
    fetch: Callable[[str, float], bytes] = _fetch,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run,
) -> dict[str, Any]:
    root = root.resolve()
    if requested_version:
        target_version = _validate_version(requested_version)
        installer_url = INSTALLER_URL.format(version=target_version)
        discovery = "explicit"
    else:
        raw = fetch(HANDOFF_URL, timeout)
        try:
            handoff = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SelfUpdateError("canonical handoff is not valid UTF-8") from exc
        target_version, installer_url = extract_pinned_release(handoff)
        discovery = "canonical-handoff"

    current_version = __version__
    base = {
        "format": "agent-devtools-self-update",
        "formatVersion": 1,
        "currentVersion": current_version,
        "targetVersion": target_version,
        "discovery": discovery,
        "installerUrl": installer_url,
    }

    if check_only:
        return {
            **base,
            "status": "update-available" if current_version != target_version else "up-to-date",
            "updateAvailable": current_version != target_version,
            "performed": False,
        }

    if current_version == target_version:
        return {
            **base,
            "status": "up-to-date",
            "updateAvailable": False,
            "performed": False,
            "installedVersion": _installed_runtime_version(root),
        }

    installer_bytes = fetch(installer_url, timeout)
    if not installer_bytes.startswith(b"#!/usr/bin/env python3"):
        raise SelfUpdateError("downloaded bootstrap does not look like the Agent DevTools Python installer")

    with tempfile.TemporaryDirectory(prefix="agent-devtools-self-update-") as td:
        installer = Path(td) / "AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py"
        installer.write_bytes(installer_bytes)

        check = runner([
            sys.executable,
            str(installer),
            "--self-check",
            "--expect-version",
            target_version,
            "--json",
        ])
        check_payload = _json_stdout(check, "installer self-check")
        if check_payload.get("status") != "pass" or check_payload.get("releaseVersion") != target_version:
            raise SelfUpdateError("installer self-check did not prove the requested release identity")

        update = runner([
            sys.executable,
            str(installer),
            "--target",
            str(root),
            "--json",
        ])
        update_payload = _json_stdout(update, "runtime update")

    installed_version = _installed_runtime_version(root)
    if installed_version != target_version:
        raise SelfUpdateError(
            f"installed runtime version mismatch: {installed_version!r} != {target_version!r}"
        )

    agent = root / "devtools" / "agent" / "agent.py"
    capabilities_proc = runner([
        sys.executable,
        str(agent),
        "capabilities",
        "--json",
    ])
    capabilities = _json_stdout(capabilities_proc, "installed capabilities")
    if capabilities.get("toolVersion") != target_version:
        raise SelfUpdateError(
            "installed capabilities.toolVersion does not match the requested release"
        )

    return {
        **base,
        "status": "updated",
        "updateAvailable": False,
        "performed": True,
        "installedVersion": installed_version,
        "installer": {
            "releaseVersion": check_payload.get("releaseVersion"),
            "embeddedKit": check_payload.get("embeddedKit"),
            "embeddedKitSha256": check_payload.get("embeddedKitSha256"),
        },
        "runtime": {
            "status": update_payload.get("status"),
            "mode": update_payload.get("mode"),
            "installedToolVersion": capabilities.get("toolVersion"),
        },
    }


_RUNTIME_UPDATE_MODES = {"off", "notify", "auto"}
_DEFAULT_RUNTIME_UPDATE_INTERVAL_HOURS = 24.0
_MIN_RUNTIME_UPDATE_INTERVAL_HOURS = 1.0
_RUNTIME_UPDATE_STATE_REL = Path(".agent-cache/runtime-update-state.json")


def load_runtime_update_policy(root: Path) -> dict[str, Any]:
    """Load fail-soft periodic runtime update policy from project config."""
    root = root.resolve()
    path = root / "agent-tools.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raw = {}
    section = raw.get("runtimeUpdate") if isinstance(raw, dict) else None
    if not isinstance(section, dict):
        return {
            "mode": "off",
            "checkIntervalHours": _DEFAULT_RUNTIME_UPDATE_INTERVAL_HOURS,
        }
    mode = str(section.get("mode") or "off").strip().lower()
    if mode not in _RUNTIME_UPDATE_MODES:
        mode = "off"
    try:
        interval = float(section.get("checkIntervalHours", _DEFAULT_RUNTIME_UPDATE_INTERVAL_HOURS))
    except (TypeError, ValueError):
        interval = _DEFAULT_RUNTIME_UPDATE_INTERVAL_HOURS
    if not math.isfinite(interval) or interval < _MIN_RUNTIME_UPDATE_INTERVAL_HOURS:
        interval = _DEFAULT_RUNTIME_UPDATE_INTERVAL_HOURS
    return {"mode": mode, "checkIntervalHours": interval}


def _runtime_update_state_path(root: Path) -> Path:
    return root.resolve() / _RUNTIME_UPDATE_STATE_REL


def _save_runtime_update_state(root: Path, payload: dict[str, Any]) -> bool:
    try:
        atomic_json_write(_runtime_update_state_path(root), payload)
    except OSError:
        return False
    return True


def _load_runtime_update_state(root: Path) -> dict[str, Any]:
    path = _runtime_update_state_path(root)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def periodic_runtime_freshness(
    root: Path,
    *,
    timeout: float = 5.0,
    now: float | None = None,
    fetch: Callable[[str, float], bytes] = _fetch,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run,
) -> dict[str, Any]:
    """Check or update runtime only when the configured TTL has expired.

    This is deliberately best-effort. Network/update failures are recorded in local
    cache state and returned as warnings; routine work must remain available.
    """
    root = root.resolve()
    policy = load_runtime_update_policy(root)
    mode = str(policy["mode"])
    interval_hours = float(policy["checkIntervalHours"])
    base = {
        "format": "agent-devtools-runtime-freshness",
        "formatVersion": 1,
        "mode": mode,
        "checkIntervalHours": interval_hours,
        "currentVersion": __version__,
        "nonBlocking": True,
    }
    if mode == "off":
        return {**base, "status": "disabled", "checked": False}

    stamp = float(time.time() if now is None else now)
    state = _load_runtime_update_state(root)
    try:
        last_checked = float(state.get("lastCheckedAtUnix"))
    except (TypeError, ValueError):
        last_checked = 0.0
    ttl_seconds = interval_hours * 3600.0
    if last_checked > 0 and stamp >= last_checked and stamp - last_checked < ttl_seconds:
        return {
            **base,
            "status": "cached",
            "checked": False,
            "lastCheckedAtUnix": last_checked,
            "cachedStatus": state.get("status"),
            "targetVersion": state.get("targetVersion"),
        }

    try:
        result = self_update(
            root,
            check_only=(mode == "notify"),
            timeout=timeout,
            fetch=fetch,
            runner=runner,
        )
    except (SelfUpdateError, OSError, subprocess.SubprocessError) as exc:
        record = {
            "format": "agent-devtools-runtime-freshness-state",
            "formatVersion": 1,
            "lastCheckedAtUnix": stamp,
            "status": "warn",
            "error": str(exc),
            "currentVersion": __version__,
        }
        recorded = _save_runtime_update_state(root, record)
        return {**base, "status": "warn", "checked": True, "error": str(exc), "stateRecorded": recorded}

    status = str(result.get("status") or "unknown")
    record = {
        "format": "agent-devtools-runtime-freshness-state",
        "formatVersion": 1,
        "lastCheckedAtUnix": stamp,
        "status": status,
        "currentVersion": result.get("currentVersion", __version__),
        "targetVersion": result.get("targetVersion"),
        "performed": bool(result.get("performed")),
    }
    recorded = _save_runtime_update_state(root, record)
    return {
        **base,
        "status": status,
        "checked": True,
        "targetVersion": result.get("targetVersion"),
        "updateAvailable": bool(result.get("updateAvailable")),
        "performed": bool(result.get("performed")),
        "installedVersion": result.get("installedVersion"),
        "stateRecorded": recorded,
    }
