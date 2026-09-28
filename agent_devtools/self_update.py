from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__

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
    return subprocess.run(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
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
