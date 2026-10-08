#!/usr/bin/env python3
"""Prepare, qualify, and optionally publish Agent DevTools in one command.

Without --publish this leaves a qualified local release candidate.
With --publish it also commits, tags, atomically pushes branch+tag, and verifies
the remote refs. The publish path starts only from a clean named branch exactly
synchronized with the selected remote.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_devtools.changes import ChangeSetError, discover_changes

VERSION_FILE = ROOT / "agent_devtools" / "__init__.py"
README = ROOT / "README.md"
HANDOFF = ROOT / "AGENT-START-HERE.md"
PUBLIC_VERSION = ROOT / "VERSION"
INSTALLER = ROOT / "bootstrap" / "AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py"
KIT = ROOT / "bootstrap" / "agent-devtools-bootstrap-kit.zip"
MANAGED_FILES = (VERSION_FILE, README, HANDOFF, PUBLIC_VERSION, INSTALLER, KIT)
SAFE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?$")
PINNED_URL = "https://raw.githubusercontent.com/Utundry/agent-devtools/v{version}/bootstrap/AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py"
DEFAULT_REMOTE = "origin"


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


def _capture(argv: Iterable[str], *, check: bool = True) -> str:
    cmd = [str(item) for item in argv]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ReleaseBuilderError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}"
            + (f": {detail}" if detail else "")
        )
    return proc.stdout.rstrip("\r\n")


def _git_head() -> str:
    return _capture(["git", "rev-parse", "HEAD"])


def _change_report() -> dict:
    try:
        report = discover_changes(ROOT)
    except ChangeSetError as exc:
        raise ReleaseBuilderError(f"cannot inspect release working tree: {exc}") from exc
    if not report.get("available"):
        raise ReleaseBuilderError(
            "cannot inspect release working tree: " + str(report.get("reason") or "unavailable")
        )
    return report


def _git_clean() -> None:
    report = _change_report()
    blocked = [
        str(row.get("path"))
        for row in report.get("entries", [])
        if not bool(row.get("workspaceLocal"))
    ]
    if blocked:
        raise ReleaseBuilderError(
            "release preparation requires no unmarked Git working-tree changes; "
            "blocked: " + ", ".join(blocked)
        )


def _current_branch() -> str:
    branch = _capture(["git", "symbolic-ref", "--quiet", "--short", "HEAD"])
    if not branch:
        raise ReleaseBuilderError("release publishing requires a named Git branch, not detached HEAD")
    return branch


def _git_is_ancestor(ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    detail = proc.stderr.strip()
    raise ReleaseBuilderError(
        "cannot compare release branch ancestry: " + (detail or "git merge-base failed")
    )


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
        raise ReleaseBuilderError(
            f"expected exactly one occurrence in {_display_path(path)}: {old!r}; found {count}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _replace_all_required(path: Path, old: str, new: str) -> int:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count < 1:
        raise ReleaseBuilderError(
            f"expected release reference not found in {_display_path(path)}: {old!r}"
        )
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
    _invalidate_version_bytecode()


def _invalidate_version_bytecode() -> None:
    # A same-size version edit within one timestamp second can otherwise keep
    # the old __version__ in child interpreters. Only this module is invalidated.
    for path in (VERSION_FILE.parent / "__pycache__").glob("__init__.*.pyc"):
        path.unlink(missing_ok=True)


def _snapshot() -> dict[Path, bytes | None]:
    return {path: (path.read_bytes() if path.exists() else None) for path in MANAGED_FILES}


def _restore(snapshot: dict[Path, bytes | None]) -> None:
    for path, data in snapshot.items():
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    _invalidate_version_bytecode()


def _tag_must_not_exist(version: str) -> None:
    if _capture(["git", "tag", "--list", f"v{version}"]):
        raise ReleaseBuilderError(f"tag v{version} already exists")


def _remote_tag_exists(remote: str, version: str) -> bool:
    proc = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--tags", remote, f"refs/tags/v{version}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 2:
        return False
    detail = proc.stderr.strip() or proc.stdout.strip()
    raise ReleaseBuilderError(
        f"cannot inspect remote tag v{version} on {remote}: {detail or 'git ls-remote failed'}"
    )


def _publish_preflight(version: str, remote: str) -> tuple[str, str]:
    branch = _current_branch()
    _run(["git", "fetch", "--prune", remote, branch])
    local_head = _git_head()
    remote_head = _capture(["git", "rev-parse", f"refs/remotes/{remote}/{branch}"])
    if local_head != remote_head and not _git_is_ancestor(remote_head, local_head):
        raise ReleaseBuilderError(
            f"local {branch} is not a fast-forward publication of {remote}/{branch}: "
            f"remote={remote_head}, local={local_head}"
        )
    if _remote_tag_exists(remote, version):
        raise ReleaseBuilderError(f"remote tag v{version} already exists on {remote}")
    return branch, local_head


def _raw_changed_paths() -> tuple[str, ...]:
    # Keep this low-level helper independent of Agent DevTools project config.
    # Release-specific callers may layer workspace-local semantics on top, while
    # generic Git parsing remains usable in minimal repositories and tests.
    result: list[str] = []
    for raw in _capture(["git", "status", "--porcelain"]).splitlines():
        if not raw.strip():
            continue
        value = raw[3:].strip() if len(raw) >= 4 else raw.strip()
        if " -> " in value:
            value = value.split(" -> ", 1)[1].strip()
        if value:
            result.append(value.replace("\\", "/"))
    return tuple(result)


def _changed_paths(*, include_workspace_local: bool = False) -> tuple[str, ...]:
    raw = _raw_changed_paths()
    if include_workspace_local or not raw:
        return raw
    try:
        report = _change_report()
    except ReleaseBuilderError:
        # Preserve the generic Git helper for minimal repositories that do not
        # have Agent DevTools project configuration.
        return raw

    # Git porcelain may collapse a fully-untracked directory to one row such
    # as ``devtools/`` while canonical change discovery expands it to exact
    # files carrying content-addressed workspace-local marks. Once project
    # configuration is available, release semantics must therefore come from
    # the canonical report rather than trying to subtract exact file marks from
    # lossy porcelain directory rows.
    material = {
        str(row.get("path") or "").replace("\\", "/")
        for row in report.get("entries", [])
        if str(row.get("path") or "").strip() and not bool(row.get("workspaceLocal"))
    }
    return tuple(sorted(material))


def _workspace_local_snapshot() -> dict[Path, tuple[bytes, int]]:
    report = _change_report()
    snapshot: dict[Path, tuple[bytes, int]] = {}
    for row in report.get("entries", []):
        if not bool(row.get("workspaceLocal")):
            continue
        rel = str(row.get("path") or "")
        path = ROOT / rel
        if path.is_file():
            snapshot[path] = (path.read_bytes(), path.stat().st_mode & 0o777)
    return snapshot


def _restore_workspace_local_snapshot(snapshot: dict[Path, tuple[bytes, int]]) -> None:
    for path, (data, mode) in snapshot.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)


def _assert_only_managed_release_changes() -> None:
    allowed = {str(path.relative_to(ROOT)).replace("\\", "/") for path in MANAGED_FILES}
    unexpected = [path for path in _changed_paths() if path not in allowed]
    if unexpected:
        raise ReleaseBuilderError(
            "release builder produced unexpected working-tree changes: " + ", ".join(unexpected)
        )


def _qualification(version: str) -> None:
    py = sys.executable
    _run([py, "scripts/build_bootstrap.py", "--version", version, "--allow-dirty"])
    _run([py, "scripts/build_bootstrap.py", "--version", version, "--check", "--allow-dirty"])
    _run([py, str(INSTALLER.relative_to(ROOT)), "--self-check", "--expect-version", version, "--json"])
    _run([py, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])
    _run([py, "-m", "compileall", "-q", "agent_devtools", "tests", "scripts"])
    _run(["git", "diff", "--check"])


def _post_commit_qualification(version: str) -> None:
    py = sys.executable
    # _git_clean() already fails closed on every unmarked or mismatched change.
    # The bootstrap builder may therefore ignore the remaining exact
    # workspace-local dirtiness without weakening release qualification.
    _git_clean()
    _run([py, "scripts/build_bootstrap.py", "--version", version, "--check", "--allow-dirty"])
    _run([py, str(INSTALLER.relative_to(ROOT)), "--self-check", "--expect-version", version, "--json"])


def _remote_refs(remote: str, branch: str, version: str) -> dict[str, str]:
    tag = f"refs/tags/v{version}"
    output = _capture([
        "git", "ls-remote", remote,
        f"refs/heads/{branch}",
        tag,
        tag + "^{}",
    ])
    refs: dict[str, str] = {}
    for raw in output.splitlines():
        parts = raw.split(None, 1)
        if len(parts) == 2:
            refs[parts[1].strip()] = parts[0].strip()
    return refs


def _rollback_local_publish(
    original_head: str,
    tag: str,
    workspace_local_snapshot: dict[Path, tuple[bytes, int]] | None = None,
) -> None:
    subprocess.run(
        ["git", "tag", "-d", tag],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(
        ["git", "reset", "--hard", original_head],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if workspace_local_snapshot:
        _restore_workspace_local_snapshot(workspace_local_snapshot)


def _publish_release(
    *,
    version: str,
    remote: str,
    branch: str,
    original_head: str,
    workspace_local_snapshot: dict[Path, tuple[bytes, int]] | None = None,
) -> dict[str, str]:
    tag = f"v{version}"
    _assert_only_managed_release_changes()
    paths = [str(path.relative_to(ROOT)) for path in MANAGED_FILES]
    try:
        _run(["git", "add", "--", *paths])
        _run(["git", "diff", "--cached", "--check"])
        _run(["git", "commit", "-m", f"Release {version}"])
        commit = _git_head()
        _run(["git", "tag", "-a", tag, "-m", f"Agent DevTools {version}"])
        _post_commit_qualification(version)
        _run([
            "git", "push", "--atomic", remote,
            f"HEAD:refs/heads/{branch}",
            f"refs/tags/{tag}:refs/tags/{tag}",
        ])
    except Exception:
        _rollback_local_publish(original_head, tag, workspace_local_snapshot)
        raise

    refs = _remote_refs(remote, branch, version)
    remote_branch = refs.get(f"refs/heads/{branch}")
    remote_tag_commit = refs.get(f"refs/tags/{tag}^{{}}")
    if remote_branch != commit or remote_tag_commit != commit:
        raise ReleaseBuilderError(
            "atomic push reported success but remote verification does not match the release commit; "
            f"local={commit}, remoteBranch={remote_branch}, remoteTagCommit={remote_tag_commit}"
        )
    return {"commit": commit, "tag": tag, "remote": remote, "branch": branch}


def _payload(
    version: str,
    previous: str,
    *,
    publication: dict[str, str] | None = None,
) -> dict[str, object]:
    changed = _changed_paths()
    payload: dict[str, object] = {
        "format": "agent-devtools-public-release-candidate",
        "formatVersion": 2,
        "status": "published" if publication else "ready",
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
        "changedFiles": list(changed),
    }
    if publication:
        payload["publication"] = publication
    return payload


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Prepare, qualify, and optionally publish Agent DevTools"
    )
    ap.add_argument("--version", required=True, help="new public version, e.g. 0.8.3")
    ap.add_argument(
        "--publish",
        action="store_true",
        help="commit, create annotated tag, atomically push branch+tag, and verify remote refs",
    )
    ap.add_argument(
        "--remote",
        default=DEFAULT_REMOTE,
        help="Git remote used by --publish (default: origin)",
    )
    ap.add_argument("--json", action="store_true", dest="json_output")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="validate preconditions and show the planned version transition without writing",
    )
    return ap


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    version = str(args.version).strip()
    original_head: str | None = None
    try:
        if not SAFE_VERSION.fullmatch(version):
            raise ReleaseBuilderError(
                "version must look like 0.8.3 or a safe prerelease variant"
            )
        if args.dry_run and args.publish:
            raise ReleaseBuilderError("--dry-run and --publish are mutually exclusive")

        _git_clean()
        workspace_local_snapshot = _workspace_local_snapshot()
        previous = _current_version()
        if version == previous:
            raise ReleaseBuilderError(
                "requested version already matches current source version"
            )
        _tag_must_not_exist(version)

        branch: str | None = None
        original_head = _git_head()
        if args.publish:
            branch, original_head = _publish_preflight(version, str(args.remote))

        if args.dry_run:
            payload: dict[str, object] = {
                "format": "agent-devtools-public-release-candidate",
                "formatVersion": 2,
                "status": "plan",
                "previousVersion": previous,
                "version": version,
                "writes": [str(path.relative_to(ROOT)) for path in MANAGED_FILES],
                "publish": bool(args.publish),
            }
        else:
            snapshot = _snapshot()
            try:
                _set_version(previous, version)
                _qualification(version)
                publication = None
                if args.publish:
                    assert branch is not None
                    publication = _publish_release(
                        version=version,
                        remote=str(args.remote),
                        branch=branch,
                        original_head=original_head,
                        workspace_local_snapshot=workspace_local_snapshot,
                    )
                payload = _payload(version, previous, publication=publication)
            except Exception:
                if original_head and _git_head() == original_head:
                    _restore(snapshot)
                raise
    except (ReleaseBuilderError, OSError, subprocess.CalledProcessError) as exc:
        failure = {
            "format": "agent-devtools-public-release-candidate",
            "formatVersion": 2,
            "status": "fail",
            "error": str(exc),
        }
        if getattr(args, "json_output", False):
            print(json.dumps(failure, ensure_ascii=False, indent=2))
        else:
            print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload["status"] == "plan":
        print(f"PLAN: Agent DevTools {previous} -> {version}")
    elif payload["status"] == "published":
        pub = payload["publication"]
        print(f"PUBLISHED: Agent DevTools {version}")
        print(f"  commit: {pub['commit']}")
        print(f"  tag: {pub['tag']}")
        print(f"  remote: {pub['remote']}/{pub['branch']}")
        print(f"  installer: {payload['installer']['sha256']} · {payload['installer']['bytes']} bytes")
        print(f"  kit: {payload['kit']['sha256']} · {payload['kit']['bytes']} bytes")
    else:
        print(f"READY: Agent DevTools {version}")
        print(f"  installer: {payload['installer']['sha256']} · {payload['installer']['bytes']} bytes")
        print(f"  kit: {payload['kit']['sha256']} · {payload['kit']['bytes']} bytes")
        print("  local candidate only; add --publish next time for zero-manual-Git release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
