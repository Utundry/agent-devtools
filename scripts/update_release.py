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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_devtools.update_scenario import (
    UpdateScenarioError,
    apply_scenario,
    load_scenario,
    scenario_marker,
)


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

def _update_home(root: Path) -> Path:
    return root / ".agent-updates"


def _current_source_version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpdateError(f"Cannot read project VERSION: {exc}") from exc


def _incoming_scenarios(root: Path) -> list:
    incoming = _update_home(root) / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    scenarios = []
    for path in sorted(incoming.glob("*.json")):
        try:
            scenarios.append(load_scenario(path))
        except UpdateScenarioError as exc:
            raise UpdateError(f"Invalid incoming scenario {path.name}: {exc}") from exc
    return scenarios


def discover_auto_chain(root: Path) -> list[Path]:
    current = _current_source_version(root)
    by_from: dict[str, list] = {}
    for scenario in _incoming_scenarios(root):
        by_from.setdefault(scenario.from_version, []).append(scenario)

    chain: list[Path] = []
    seen = {current}
    while current in by_from:
        choices = by_from[current]
        if len(choices) != 1:
            names = ", ".join(sorted(item.path.name for item in choices))
            raise UpdateError(
                f"Ambiguous update chain from VERSION {current}: {names}"
            )
        scenario = choices[0]
        if scenario.to_version in seen:
            raise UpdateError(
                f"Update scenario cycle detected at VERSION {scenario.to_version}"
            )
        chain.append(scenario.path)
        seen.add(scenario.to_version)
        current = scenario.to_version
    return chain


def _archive_applied_scenario(root: Path, scenario_path: Path, digest: str) -> Path:
    applied = _update_home(root) / "applied"
    applied.mkdir(parents=True, exist_ok=True)
    destination = applied / f"{scenario_path.stem}-{digest[:12]}.json"
    if destination.exists():
        existing = hashlib.sha256(destination.read_bytes()).hexdigest()
        if existing != digest:
            raise UpdateError(f"Applied scenario archive collision: {destination.name}")
        scenario_path.unlink(missing_ok=True)
        return destination
    shutil.move(str(scenario_path), str(destination))
    return destination


def _auto_catalog_key(root: Path) -> str:
    digest = hashlib.sha256()
    digest.update((_current_source_version(root) + "\0").encode("utf-8"))
    incoming = _update_home(root) / "incoming"
    if incoming.is_dir():
        for path in sorted(incoming.glob("*.json")):
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _periodic_state_path(root: Path) -> Path:
    return _update_home(root) / "periodic-state.json"


def _load_periodic_state(root: Path) -> dict:
    path = _periodic_state_path(root)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_periodic_failure(root: Path, key: str, error: str) -> None:
    path = _periodic_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({
        "format": "agent-devtools-periodic-update-state",
        "formatVersion": 1,
        "failedKey": key,
        "error": error,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _clear_periodic_state(root: Path) -> None:
    _periodic_state_path(root).unlink(missing_ok=True)


def auto_update(args: argparse.Namespace) -> dict:
    if not getattr(args, "publish", False):
        raise UpdateError("--auto requires --publish so each successful scenario advances the canonical worktree")
    if getattr(args, "version", None):
        raise UpdateError("--version cannot be combined with --auto")
    entry = Path(args.repo).expanduser().resolve()
    root = branch_worktree(entry, args.branch)
    ensure_idle(root)
    started = _current_source_version(root)
    reports = []

    while True:
        chain = discover_auto_chain(root)
        if not chain:
            break
        scenario_path = chain[0]
        scenario = load_scenario(scenario_path)
        values = vars(args).copy()
        values.update({
            "version": None,
            "patch": None,
            "scenario": str(scenario_path),
            "auto": False,
            "periodic": None,
        })
        report = update(argparse.Namespace(**values))
        _archive_applied_scenario(root, scenario_path, scenario.digest)
        reports.append(report)

    return {
        "status": "updated" if reports else "idle",
        "fromVersion": started,
        "version": _current_source_version(root),
        "appliedCount": len(reports),
        "reports": reports,
    }


def periodic_update(args: argparse.Namespace, *, sleep_fn=time.sleep, max_cycles: int | None = None) -> None:
    interval = float(getattr(args, "periodic", 0) or 0)
    if interval < 1.0:
        raise UpdateError("--periodic interval must be at least 1 second")
    if not getattr(args, "publish", False):
        raise UpdateError("--periodic requires --publish")
    if getattr(args, "version", None):
        raise UpdateError("--version cannot be combined with --periodic")

    entry = Path(args.repo).expanduser().resolve()
    root = branch_worktree(entry, args.branch)
    cycles = 0
    while True:
        incoming = _update_home(root) / "incoming"
        has_scenarios = incoming.is_dir() and any(incoming.glob("*.json"))
        if has_scenarios:
            key = _auto_catalog_key(root)
            state = _load_periodic_state(root)
            suppressed = (
                not getattr(args, "retry_failed", False)
                and state.get("failedKey") == key
            )
            if not suppressed:
                values = vars(args).copy()
                values["auto"] = True
                values["periodic"] = None
                try:
                    report = auto_update(argparse.Namespace(**values))
                except (UpdateError, UpdateScenarioError, OSError, subprocess.SubprocessError) as exc:
                    _save_periodic_failure(root, key, str(exc))
                    print(f"Periodic update failed and is suppressed until input or VERSION changes: {exc}", file=sys.stderr, flush=True)
                else:
                    _clear_periodic_state(root)
                    if report["appliedCount"]:
                        print(json.dumps(report, indent=2), flush=True)

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return
        sleep_fn(interval)


def update(args: argparse.Namespace) -> dict:
    scenario_path_raw = getattr(args, "scenario", None)
    scenario = load_scenario(Path(scenario_path_raw)) if scenario_path_raw else None
    requested_version = getattr(args, "version", None)
    version = scenario.to_version if scenario is not None else str(requested_version or "")
    if scenario is not None and requested_version and requested_version != scenario.to_version:
        raise UpdateError(
            f"--version {requested_version} does not match scenario toVersion {scenario.to_version}"
        )
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9._-]+)?", version):
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
    patch = Path(args.patch).expanduser().resolve() if getattr(args, "patch", None) else None
    scenario_path = Path(scenario_path_raw).expanduser().resolve() if scenario_path_raw else None
    if patch is not None and scenario is not None:
        raise UpdateError("--patch and --scenario are mutually exclusive")
    if patch is not None and not patch.is_file():
        raise UpdateError(f"Patch not found: {patch}")
    if scenario_path is not None and not scenario_path.is_file():
        raise UpdateError(f"Scenario not found: {scenario_path}")
    output = root / ".agent-updates" / "runs"
    output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=f"update-{version}-", dir=output))
    clone = run / "repo"
    if scenario is not None:
        shutil.copy2(scenario.path, run / "scenario.json")
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

    tag = f"refs/tags/v{version}"
    refs = dict(
        reversed(line.split("\t", 1))
        for line in git(clone, "ls-remote", args.remote, tag, tag + "^{}").stdout.splitlines()
    )
    published = refs.get(tag + "^{}", refs.get(tag))
    patch_status = "not-requested"
    scenario_status = "not-requested"
    if published:
        git(clone, "fetch", "--quiet", args.remote, tag)
        if not ancestor(clone, published, remote_head):
            raise UpdateError("Existing release tag does not belong to the remote branch")
        if value(clone, "show", f"{published}:VERSION") != version:
            raise UpdateError("Existing release tag records another version")
        if patch:
            marker = patch_marker(patch)
            recorded = marker in git(clone, "log", published, "--format=%B", "--fixed-strings", "--grep", marker).stdout.splitlines()
            reversible = git(clone, "apply", "--reverse", "--check", "--binary", str(patch), check=False).returncode == 0
            if not recorded and not reversible:
                raise UpdateError("Cannot verify the supplied patch in this release; use a new version")
        if scenario is not None:
            marker = scenario_marker(scenario)
            recorded = marker in git(clone, "log", published, "--format=%B", "--fixed-strings", "--grep", marker).stdout.splitlines()
            if not recorded:
                raise UpdateError("Cannot verify the supplied scenario in this release; use a new version")
            scenario_status = "already-applied"
        print(f"Release v{version} already published; no rebuild", flush=True)
        status = "already-published"
    else:
        if patch:
            patch_status = apply_patch(clone, patch)
        if scenario is not None:
            marker = scenario_marker(scenario)
            recorded = marker in git(clone, "log", "HEAD", "--format=%B", "--fixed-strings", "--grep", marker).stdout.splitlines()
            if recorded:
                if value(clone, "show", "HEAD:VERSION") != scenario.to_version:
                    raise UpdateError(
                        f"Scenario provenance is present, but source VERSION is not {scenario.to_version}"
                    )
                scenario_status = "already-applied"
            else:
                source_version = value(clone, "show", "HEAD:VERSION")
                if source_version != scenario.from_version:
                    raise UpdateError(
                        f"Scenario expects source VERSION {scenario.from_version}, found {source_version}"
                    )
                try:
                    result = apply_scenario(clone, scenario)
                except UpdateScenarioError as exc:
                    raise UpdateError(str(exc)) from exc
                git(clone, "add", "-A")
                git(clone, "commit", "--allow-empty", "-m", scenario.commit_message, "-m", marker)
                scenario_status = "applied" if result["applied"] else "already-applied"
        builder = clone / "scripts" / "build_release.py"
        if not builder.is_file():
            raise UpdateError("Canonical scripts/build_release.py is missing")
        if value(clone, "show", "HEAD:VERSION") == version:
            raise UpdateError("Source already has this version without a published tag; choose the next version")
        command = [sys.executable, str(builder), "--version", version]
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

    backup = synchronize(root, clone, args.branch, head, version) if args.publish else None
    if args.publish:
        git(root, "fetch", "--quiet", "--prune", args.remote, args.branch)
    report = {
        "status": status, "version": version, "branch": args.branch,
        "originalWorktree": str(root), "head": value(root, "rev-parse", "HEAD"),
        "patch": patch_status, "scenario": scenario_status,
        "scenarioSha256": scenario.digest if scenario is not None else None,
        "localBackupStash": backup, "artifacts": str(run),
    }
    (run / "update-result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(clone)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="any worktree of the source repository")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--version", help="target version; optional when --scenario provides toVersion")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--patch", help="optional binary Git patch to apply to committed source")
    source.add_argument("--scenario", help="declarative JSON update scenario")
    source.add_argument("--auto", action="store_true", help="apply the maximal unambiguous scenario chain from .agent-updates/incoming")
    source.add_argument("--periodic", type=float, metavar="SECONDS", help="stay in foreground and run --auto at this interval")
    parser.add_argument("--retry-failed", action="store_true", help="in periodic mode retry an unchanged failed scenario/catalog")
    parser.add_argument("--publish", action="store_true", help="publish and update the original branch")
    args = parser.parse_args(argv)
    try:
        if args.periodic is not None:
            periodic_update(args)
            return 0
        report = auto_update(args) if args.auto else update(args)
    except KeyboardInterrupt:
        print("Periodic update stopped", file=sys.stderr)
        return 130
    except (UpdateError, UpdateScenarioError, OSError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
