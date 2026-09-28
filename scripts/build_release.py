#!/usr/bin/env python3
"""Prepare and qualify a public Agent DevTools release candidate in one command.

This is intentionally a builder, not a publisher. It updates version-facing source
metadata, rebuilds deterministic bootstrap artifacts, runs local qualification and
leaves the exact candidate in the working tree for human review/commit/tag.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "agent_devtools" / "__init__.py"
README = ROOT / "README.md"
HANDOFF = ROOT / "AGENT-START-HERE.md"
PUBLIC_VERSION = ROOT / "VERSION"
INSTALLER = ROOT / "bootstrap" / "AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py"
KIT = ROOT / "bootstrap" / "agent-devtools-bootstrap-kit.zip"
MANAGED_FILES = (VERSION_FILE, README, HANDOFF, PUBLIC_VERSION, INSTALLER, KIT)
SAFE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?$")
PINNED_URL = "https://raw.githubusercontent.com/Utundry/agent-devtools/v{version}/bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py"


class ReleaseBuilderError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _run(argv: Iterable[str]) -> None:
    cmd = [str(item) for item in argv]
    print("$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def _git_clean() -> None:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ReleaseBuilderError("cannot inspect Git working tree")
    if proc.stdout.strip():
        raise ReleaseBuilderError("release preparation requires a clean Git working tree")


def _current_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', text, re.MULTILINE)
    if not match:
        raise ReleaseBuilderError("cannot read agent_devtools.__version__")
    return match.group(1)


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ReleaseBuilderError(f"expected exactly one occurrence in {_display_path(path)}: {old!r}; found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _replace_all_required(path: Path, old: str, new: str) -> int:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count < 1:
        raise ReleaseBuilderError(f"expected release reference not found in {_display_path(path)}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")
    return count


def _set_version(current: str, requested: str) -> None:
    _replace_once(VERSION_FILE, f'__version__ = "{current}"', f'__version__ = "{requested}"')
    _replace_once(README, f"Current version: **{current}**.", f"Current version: **{requested}**.")
    old_url = PINNED_URL.format(version=current)
    new_url = PINNED_URL.format(version=requested)
    _replace_all_required(README, old_url, new_url)
    _replace_all_required(HANDOFF, old_url, new_url)
    PUBLIC_VERSION.write_text(requested + "\n", encoding="utf-8")


def _snapshot() -> dict[Path, bytes | None]:
    return {path: (path.read_bytes() if path.exists() else None) for path in MANAGED_FILES}


def _restore(snapshot: dict[Path, bytes | None]) -> None:
    for path, data in snapshot.items():
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def _tag_must_not_exist(version: str) -> None:
    proc = subprocess.run(
        ["git", "tag", "--list", f"v{version}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ReleaseBuilderError("cannot inspect Git tags")
    if proc.stdout.strip():
        raise ReleaseBuilderError(f"tag v{version} already exists")


def _qualification(version: str) -> None:
    py = sys.executable
    _run([py, "scripts/build_bootstrap.py", "--version", version, "--allow-dirty"])
    _run([py, "scripts/build_bootstrap.py", "--version", version, "--check", "--allow-dirty"])
    _run([py, str(INSTALLER.relative_to(ROOT)), "--self-check", "--expect-version", version, "--json"])
    _run([py, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])
    _run([py, "-m", "compileall", "-q", "agent_devtools", "tests", "scripts"])
    _run(["git", "diff", "--check"])


def _payload(version: str, previous: str) -> dict[str, object]:
    proc = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return {
        "format": "agent-devtools-public-release-candidate",
        "formatVersion": 1,
        "status": "ready",
        "previousVersion": previous,
        "version": version,
        "installer": {
            "path": str(INSTALLER.relative_to(ROOT)),
            "sha256": _sha256(INSTALLER),
            "bytes": INSTALLER.stat().st_size,
        },
        "kit": {
            "path": str(KIT.relative_to(ROOT)),
            "sha256": _sha256(KIT),
            "bytes": KIT.stat().st_size,
        },
        "changedFiles": [line[3:] for line in proc.stdout.splitlines() if len(line) >= 4],
        "publishCommands": [
            f"git add {' '.join(str(p.relative_to(ROOT)) for p in MANAGED_FILES)}",
            f'git commit -m "Release {version}"',
            f'git tag -a v{version} -m "Agent DevTools {version}"',
            "git push",
            f"git push origin v{version}",
        ],
    }


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Prepare and qualify a public Agent DevTools release candidate")
    ap.add_argument("--version", required=True, help="new public version, e.g. 0.8.2")
    ap.add_argument("--json", action="store_true", dest="json_output")
    ap.add_argument("--dry-run", action="store_true", help="validate preconditions and show the planned version transition without writing")
    return ap


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    version = str(args.version).strip()
    try:
        if not SAFE_VERSION.fullmatch(version):
            raise ReleaseBuilderError("version must look like 0.8.2 or a safe prerelease variant")
        _git_clean()
        previous = _current_version()
        if version == previous:
            raise ReleaseBuilderError("requested version already matches current source version")
        _tag_must_not_exist(version)
        if args.dry_run:
            payload = {
                "format": "agent-devtools-public-release-candidate",
                "formatVersion": 1,
                "status": "plan",
                "previousVersion": previous,
                "version": version,
                "writes": [str(p.relative_to(ROOT)) for p in MANAGED_FILES],
            }
        else:
            snapshot = _snapshot()
            try:
                _set_version(previous, version)
                _qualification(version)
                payload = _payload(version, previous)
            except Exception:
                _restore(snapshot)
                raise
    except (ReleaseBuilderError, OSError, subprocess.CalledProcessError) as exc:
        if getattr(args, "json_output", False):
            print(json.dumps({"format": "agent-devtools-public-release-candidate", "formatVersion": 1, "status": "fail", "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload["status"] == "plan":
        print(f"PLAN: Agent DevTools {previous} -> {version}")
    else:
        print(f"READY: Agent DevTools {version}")
        print(f"  installer: {payload['installer']['sha256']} · {payload['installer']['bytes']} bytes")
        print(f"  kit: {payload['kit']['sha256']} · {payload['kit']['bytes']} bytes")
        print("  review/commit/tag explicitly; nothing was pushed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
