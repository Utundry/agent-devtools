#!/usr/bin/env python3
"""One-command update using the canonical release builder, Python stdlib + Git.

The preparation clone isolates existing edits and installed tooling. Only a
qualified release is published; the original branch is updated by fast-forward.
Local edits that obstruct that update are saved in an explicitly reported stash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class UpdateError(RuntimeError):
    pass


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if check and result.returncode:
        raise UpdateError(result.stderr.strip() or result.stdout.strip() or "Git failed")
    return result


def value(root: Path, *args: str) -> str:
    return git(root, *args).stdout.strip()


def ancestor(root: Path, older: str, newer: str) -> bool:
    result = git(root, "merge-base", "--is-ancestor", older, newer, check=False)
    if result.returncode not in (0, 1):
        raise UpdateError(result.stderr.strip())
    return result.returncode == 0


def branch_worktree(root: Path, branch: str) -> Path:
    path = None
    for field in git(root, "worktree", "list", "--porcelain", "-z").stdout.split("\0"):
        if field.startswith("worktree "):
            path = Path(field[9:])
        elif field == "branch refs/heads/" + branch and path is not None:
            return path.resolve()
    raise UpdateError(f"No checked-out worktree for {branch}; select it with --branch")


def ensure_idle(root: Path) -> None:
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        raw = Path(value(root, "rev-parse", "--git-path", name))
        if (raw if raw.is_absolute() else root / raw).exists():
            raise UpdateError(f"Finish the existing Git operation first: {name}")


def patch_marker(patch: Path) -> str:
    digest = hashlib.sha256()
    with patch.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "Agent-DevTools-Patch-SHA256: " + digest.hexdigest()


def apply_patch(root: Path, patch: Path) -> str:
    marker = patch_marker(patch)
    if git(root, "apply", "--check", "--binary", str(patch), check=False).returncode == 0:
        git(root, "apply", "--binary", str(patch))
        git(root, "add", "-A")  # The clone contains only committed source + this patch.
        git(root, "commit", "-m", "Apply Agent DevTools update patch", "-m", marker)
        return "applied"
    if git(root, "apply", "--reverse", "--check", "--binary", str(patch), check=False).returncode == 0:
        # Keep exact patch provenance through subsequent managed version changes.
        git(root, "commit", "--allow-empty", "-m", "Record applied Agent DevTools patch", "-m", marker)
        return "already-applied"
    raise UpdateError("Patch does not match the committed branch; original files are unchanged")


def synchronize(root: Path, clone: Path, branch: str, expected_head: str, version: str) -> str | None:
    if value(root, "rev-parse", "HEAD") != expected_head:
        raise UpdateError("Release published, but the original HEAD changed during preparation; update stopped")
    git(root, "fetch", "--quiet", str(clone), f"refs/heads/{branch}")
    target = value(root, "rev-parse", "FETCH_HEAD")
    if not ancestor(root, expected_head, target):
        raise UpdateError("Published branch is not a fast-forward of the original HEAD")
    result = git(root, "merge", "--ff-only", target, check=False)
    if result.returncode == 0:
        return None
    if not git(root, "status", "--porcelain", "-z").stdout:
        raise UpdateError(result.stderr.strip() or result.stdout.strip())
    previous = git(root, "rev-parse", "--verify", "refs/stash", check=False).stdout.strip()
    git(root, "stash", "push", "--include-untracked", "-m", f"Before automated release {version}")
    backup = value(root, "rev-parse", "refs/stash")
    if backup == previous:
        raise UpdateError("Local backup was not created; original branch update stopped")
    print(f"Local edits saved in stash {backup}; retained without automatic apply", flush=True)
    git(root, "merge", "--ff-only", target)
    return backup


def update(args: argparse.Namespace) -> dict:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?", args.version):
        raise UpdateError("Invalid release version")
    entry = Path(args.repo).expanduser().resolve()
    root = branch_worktree(entry, args.branch)
    ensure_idle(root)
    head = value(root, "rev-parse", "HEAD")
    remote_url = value(root, "remote", "get-url", "--push", args.remote)
    # A relative local remote is relative to the original repository, not clone.
    if not re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", remote_url) and ":" not in remote_url:
        remote_url = str((root / remote_url).resolve())
    identity = value(root, "var", "GIT_AUTHOR_IDENT")
    name, email = re.match(r"^(.*) <([^>]+)>", identity).groups()
    patch = Path(args.patch).expanduser().resolve() if args.patch else None
    if patch is not None and not patch.is_file():
        raise UpdateError(f"Patch not found: {patch}")
    output = root / "build"
    output.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=f"update-{args.version}-", dir=output))
    clone = run / "repo"
    print(f"Branch: {args.branch}; original worktree: {root}", flush=True)
    print(f"Preparing committed source; log and artifacts: {run}", flush=True)
    git(root, "clone", "--quiet", "--no-local", "--no-checkout", str(root), str(clone))
    git(clone, "checkout", "--quiet", "-B", args.branch, head)
    git(clone, "config", "user.name", name)
    git(clone, "config", "user.email", email)
    if args.remote != "origin":
        git(clone, "remote", "rename", "origin", args.remote)
    git(clone, "remote", "set-url", args.remote, remote_url)
    git(clone, "fetch", "--quiet", "--prune", args.remote, args.branch)
    remote_head = value(clone, "rev-parse", "FETCH_HEAD")
    if ancestor(clone, head, remote_head):
        git(clone, "merge", "--ff-only", remote_head)
    elif not ancestor(clone, remote_head, head):
        raise UpdateError("Local and remote branches diverged; original files are unchanged")

    tag = f"refs/tags/v{args.version}"
    refs = dict(
        reversed(line.split("\t", 1))
        for line in git(clone, "ls-remote", args.remote, tag, tag + "^{}").stdout.splitlines()
    )
    published = refs.get(tag + "^{}", refs.get(tag))
    patch_status = "not-requested"
    if published:
        git(clone, "fetch", "--quiet", args.remote, tag)
        if not ancestor(clone, published, remote_head):
            raise UpdateError("Existing release tag does not belong to the remote branch")
        if value(clone, "show", f"{published}:VERSION") != args.version:
            raise UpdateError("Existing release tag records another version")
        if patch:
            marker = patch_marker(patch)
            recorded = marker in git(clone, "log", published, "--format=%B", "--fixed-strings", "--grep", marker).stdout.splitlines()
            reversible = git(clone, "apply", "--reverse", "--check", "--binary", str(patch), check=False).returncode == 0
            if not recorded and not reversible:
                raise UpdateError("Cannot verify the supplied patch in this release; use a new version")
        print(f"Release v{args.version} already published; no rebuild", flush=True)
        status = "already-published"
    else:
        if patch:
            patch_status = apply_patch(clone, patch)
        builder = clone / "scripts" / "build_release.py"
        if not builder.is_file():
            raise UpdateError("Canonical scripts/build_release.py is missing")
        if value(clone, "show", "HEAD:VERSION") == args.version:
            raise UpdateError("Source already has this version without a published tag; choose the next version")
        command = [sys.executable, str(builder), "--version", args.version]
        if args.publish:
            command += ["--publish", "--remote", args.remote]
        print("Running canonical release builder (bootstrap, tests, commit/tag/push)", flush=True)
        log = run / "build-release.log"
        with log.open("w", encoding="utf-8") as handle:
            result = subprocess.run(command, cwd=clone, stdout=handle, stderr=subprocess.STDOUT)
        if result.returncode:
            print(log.read_text(encoding="utf-8", errors="replace")[-6000:], file=sys.stderr)
            raise UpdateError(f"Release builder failed; original worktree unchanged. Log: {log}")
        for filename in ("AGENT-DEVTOOLS-BOOTSTRAP-RUN-ME.py", "agent-devtools-bootstrap-kit.zip"):
            shutil.copy2(clone / "bootstrap" / filename, run / filename)
        status = "published" if args.publish else "prepared"

    backup = synchronize(root, clone, args.branch, head, args.version) if args.publish else None
    if args.publish:
        git(root, "fetch", "--quiet", "--prune", args.remote, args.branch)
    report = {
        "status": status, "version": args.version, "branch": args.branch,
        "originalWorktree": str(root), "head": value(root, "rev-parse", "HEAD"),
        "patch": patch_status, "localBackupStash": backup, "artifacts": str(run),
    }
    (run / "update-result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(clone)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="any worktree of the source repository")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--version", required=True)
    parser.add_argument("--patch", help="optional binary Git patch to apply to committed source")
    parser.add_argument("--publish", action="store_true", help="publish and update the original branch")
    args = parser.parse_args(argv)
    try:
        report = update(args)
    except (UpdateError, OSError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
