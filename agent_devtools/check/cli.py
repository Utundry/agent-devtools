from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent_devtools.core.workspace import WorkspaceError, active_status, cancel_active, default_work_root

from .changes import git_changed_files
from .config import CheckConfig, CheckConfigError, load_check_config
from .policy import PolicyError, SelectionPlan, load_policy
from .runner import run_plan
from .certification import certify_plan
from .replay import ReplayError, run_exact_replay
from .selection import apply_selection_safety_guards, render_selection_plan


def _add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base", default=None, help="Git base revision for affected selection")
    parser.add_argument("--changed", action="append", default=[], help="explicit changed path; repeat as needed")
    parser.add_argument("--profile", default="affected", help="policy profile (default: affected)")
    parser.add_argument("--config", type=Path, default=None)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="check_command", required=True)
    plan = sub.add_parser("plan", help="compute an explainable verification selection plan")
    _add_selection_args(plan)
    plan.add_argument("--explain", action="store_true")
    plan.add_argument("--json", action="store_true", dest="json_output")

    run = sub.add_parser("run", help="execute selected declarative suite commands")
    _add_selection_args(run)
    run.add_argument("--no-cache", action="store_true", help="bypass ordinary development-stage cache")
    run.add_argument("--resume", action="store_true", help="reuse compatible PASS chunks from the latest interrupted/failed run")
    run.add_argument("--max-chunks", type=int, default=None, help="execute at most N new chunks in this invocation, then return PARTIAL")
    run.add_argument("--time-slice-seconds", type=float, default=None, help="stop between chunks after the approximate invocation budget is reached")
    run.add_argument("--json", action="store_true", dest="json_output")

    certify = sub.add_parser("certify", help="run the complete certification profile with reusable certified PASS evidence")
    certify.add_argument("--config", type=Path, default=None)
    certify.add_argument("--cold", action="store_true", help="physically execute all certification work and ignore certified evidence")
    certify.add_argument("--resume", action="store_true", help="reuse compatible PASS chunks from the latest partial/interrupted certification run")
    certify.add_argument("--max-chunks", type=int, default=None, help="execute at most N new chunks in this certification invocation")
    certify.add_argument("--time-slice-seconds", type=float, default=None, help="stop between chunks after the approximate certification invocation budget is reached")
    certify.add_argument("--json", action="store_true", dest="json_output")

    replay = sub.add_parser("replay", help="prove exact source replay from a known base snapshot")
    replay.add_argument("--base", type=Path, required=True, help="base source directory or ZIP snapshot")
    replay.add_argument("--config", type=Path, default=None)
    replay.add_argument("--bundle-out", type=Path, default=None, help="write the deterministic exact replay bundle")
    replay.add_argument("--source-only", action="store_true", help="verify exact source bytes without semantic certification/generated output comparison")
    replay.add_argument("--json", action="store_true", dest="json_output")

    start = sub.add_parser("start", help="launch check run detached and return immediately")
    _add_selection_args(start)
    start.add_argument("--no-cache", action="store_true", help="bypass ordinary development-stage cache")
    start.add_argument("--resume", action="store_true", help="resume compatible PASS chunks when launching an affected run")
    start.add_argument("--max-chunks", type=int, default=None, help="execute at most N new chunks in this invocation")
    start.add_argument("--time-slice-seconds", type=float, default=None, help="stop between chunks after the approximate invocation budget is reached")
    start.add_argument("--certify", action="store_true", help="launch `agent check certify` instead of an affected run")
    start.add_argument("--cold", action="store_true", help="with --certify, physically execute all certification work")

    sub.add_parser("status", help="show an active local check run")
    sub.add_parser("cancel", help="request cancellation of the active local check run")


def _fail_safe_payload(config: CheckConfig, *, profile: str, base: str | None, reasons: list[str], changed: list[str] | None = None) -> dict:
    return {
        "profile": profile,
        "base": base or "HEAD",
        "changedFiles": changed or [],
        "fallbackFull": True,
        "fallbackReasons": reasons,
        "selectedSuites": [
            {"id": suite, "reason": "fail-safe full selection", "hit": []}
            for suite in config.suite_order
        ],
        "skippedSuites": [],
    }


def _selection(root: Path, args: argparse.Namespace) -> tuple[CheckConfig, SelectionPlan | None, dict | None]:
    config = load_check_config(root, args.config)
    explicit = {str(item).replace("\\", "/") for item in (args.changed or []) if str(item).strip()}
    changed = explicit if explicit else git_changed_files(root, args.base)
    if changed is None:
        return config, None, _fail_safe_payload(
            config,
            profile=args.profile,
            base=args.base,
            reasons=["git change detection unavailable or base ref invalid"],
        )
    try:
        policy = load_policy(config.policy_path)
        return config, apply_selection_safety_guards(policy.plan(args.profile, changed), config), None
    except PolicyError as exc:
        return config, None, _fail_safe_payload(
            config,
            profile=args.profile,
            base=args.base,
            changed=sorted(changed),
            reasons=[f"policy error: {exc}"],
        )


def command_plan(root: Path, args: argparse.Namespace) -> int:
    try:
        _config, plan, fallback = _selection(root, args)
    except CheckConfigError as exc:
        print(f"agent check plan: {exc}")
        return 2

    if fallback is not None:
        if args.json_output:
            print(json.dumps(fallback, ensure_ascii=False, indent=2))
        else:
            print(
                f"PLAN {args.profile} · base={args.base or 'HEAD'} · FALLBACK FULL · "
                + "; ".join(fallback["fallbackReasons"])
            )
            if args.explain:
                print("select: " + ", ".join(item["id"] for item in fallback["selectedSuites"]))
        return 0

    assert plan is not None
    payload = plan.to_dict() | {"base": args.base or "HEAD"}
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_selection_plan(plan, base=args.base, explain=args.explain))
    return 0


def command_run(root: Path, args: argparse.Namespace) -> int:
    try:
        config, plan, fallback = _selection(root, args)
    except CheckConfigError as exc:
        print(f"agent check run: {exc}")
        return 2
    if fallback is not None:
        try:
            policy = load_policy(config.policy_path)
            # Full-fallback execution deliberately uses a policy plan only as a container,
            # then safety guards force the configured complete suite order.
            changed = fallback.get("changedFiles") or ["__agent_devtools_unknown_change__"]
            plan = apply_selection_safety_guards(policy.plan(args.profile, changed), config)
        except PolicyError as exc:
            print(f"agent check run: cannot construct fail-safe plan: {exc}")
            return 2
    assert plan is not None
    try:
        if args.max_chunks is not None and args.max_chunks <= 0:
            print("agent check run: --max-chunks must be positive")
            return 2
        if args.time_slice_seconds is not None and args.time_slice_seconds <= 0:
            print("agent check run: --time-slice-seconds must be positive")
            return 2
        code, report = run_plan(
            root,
            config,
            plan,
            cache_enabled=not args.no_cache,
            resume=bool(args.resume),
            max_chunks=args.max_chunks,
            time_slice_seconds=args.time_slice_seconds,
        )
    except WorkspaceError as exc:
        print(f"agent check run: {exc}")
        return 2
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        selected = [str(item.get("id")) for item in plan.selected]
        status = str(report.get("status") or "unknown").upper()
        print(f"{status}: {len(selected)} selected suite(s) · report={report.get('runDirectory')}/report.json")
        for suite in selected:
            item = (report.get("checks") or {}).get(suite) or {}
            cache = ((item.get("cache") or {}).get("status"))
            suffix = f" · cache={cache}" if cache else ""
            print(f"  {suite}: {item.get('status', 'not-run')}{suffix}")
        if report.get("message"):
            print(f"  error: {report['message']}")
    return code



def command_certify(root: Path, args: argparse.Namespace) -> int:
    try:
        config = load_check_config(root, args.config)
        policy = load_policy(config.policy_path)
        profile_id = config.certification.profile
        profile = policy.profiles.get(profile_id)
        if profile is None:
            raise PolicyError(f"Unknown certification profile: {profile_id}")
        if profile.selection != "all":
            raise PolicyError(f"Certification profile {profile_id!r} must use selection='all'")
        plan = apply_selection_safety_guards(policy.plan(profile_id, ()), config)
        selected = [str(item.get("id")) for item in plan.selected]
        if selected != list(config.suite_order):
            raise PolicyError(
                "Certification profile must select the complete configured suiteOrder; "
                f"selected={selected}, suiteOrder={list(config.suite_order)}"
            )
        if args.max_chunks is not None and args.max_chunks <= 0:
            raise CheckConfigError("--max-chunks must be positive")
        if args.time_slice_seconds is not None and args.time_slice_seconds <= 0:
            raise CheckConfigError("--time-slice-seconds must be positive")
        code, report = certify_plan(
            root,
            config,
            plan,
            cold=bool(args.cold),
            resume=bool(args.resume),
            max_chunks=args.max_chunks,
            time_slice_seconds=args.time_slice_seconds,
        )
    except (CheckConfigError, PolicyError, WorkspaceError) as exc:
        print(f"agent check certify: {exc}")
        return 2

    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        cert = report.get("certification") or {}
        status = str(report.get("status") or "unknown").upper()
        print(
            f"{status}: certify {cert.get('mode', 'unknown')} · "
            f"evidence {cert.get('executedEvidence', 0)} exec/{cert.get('reusedEvidence', 0)} reuse · "
            f"saved≈{float(cert.get('estimatedSavedSeconds') or 0.0):.3f}s · "
            f"report={report.get('runDirectory')}/report.json"
        )
        for suite in [str(item.get("id")) for item in plan.selected]:
            item = (report.get("checks") or {}).get(suite) or {}
            evidence = item.get("certifiedEvidence") or {}
            suffix = evidence.get("status") if isinstance(evidence, dict) else evidence
            print(f"  {suite}: {item.get('status', 'not-run')} · evidence={suffix}")
        if report.get("message"):
            print(f"  error: {report['message']}")
    return code


def command_replay(root: Path, args: argparse.Namespace) -> int:
    try:
        code, report = run_exact_replay(
            target_root=root,
            base=args.base,
            config_path=args.config,
            bundle_out=args.bundle_out,
            source_only=bool(args.source_only),
        )
    except (ReplayError, CheckConfigError, PolicyError, WorkspaceError) as exc:
        print(f"agent check replay: {exc}")
        return 2

    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status = str(report.get("status") or "unknown").upper()
        source = report.get("source") or {}
        bundle = report.get("bundle") or {}
        print(
            f"{status}: exact replay · source {source.get('identical', 0)}/{source.get('targetFiles', 0)} identical · "
            f"delta +{bundle.get('added', 0)} ~{bundle.get('changed', 0)} -{bundle.get('deleted', 0)} · "
            f"bundleSha256={bundle.get('sha256', '')}"
        )
        if bundle.get("path"):
            print(f"  bundle: {bundle['path']}")
        generated = report.get("generated")
        if isinstance(generated, dict):
            print(
                f"  generated: {generated.get('matching', 0)}/{max(generated.get('targetFiles', 0), generated.get('replayFiles', 0))} matching"
            )
        if report.get("stage"):
            print(f"  failed stage: {report['stage']}")
    return code


def command_start(root: Path, args: argparse.Namespace) -> int:
    if active_status(root) is not None:
        print("agent check start: another Agent DevTools run is already active")
        return 2
    work_root = default_work_root(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    launcher_log = work_root / f"launcher-{stamp}-{os.getpid():x}.log"
    if args.certify:
        command = [sys.executable, "-m", "agent_devtools", "check", "certify"]
        if args.config:
            command.extend(["--config", str(args.config)])
        if args.cold:
            command.append("--cold")
        if args.resume:
            command.append("--resume")
        if args.max_chunks is not None:
            command.extend(["--max-chunks", str(args.max_chunks)])
        if args.time_slice_seconds is not None:
            command.extend(["--time-slice-seconds", str(args.time_slice_seconds)])
    else:
        if args.cold:
            print("agent check start: --cold requires --certify")
            return 2
        command = [sys.executable, "-m", "agent_devtools", "check", "run", "--profile", args.profile]
        if args.base:
            command.extend(["--base", args.base])
        for changed in args.changed or []:
            command.extend(["--changed", changed])
        if args.config:
            command.extend(["--config", str(args.config)])
        if args.no_cache:
            command.append("--no-cache")
        if args.resume:
            command.append("--resume")
        if args.max_chunks is not None:
            command.extend(["--max-chunks", str(args.max_chunks)])
        if args.time_slice_seconds is not None:
            command.extend(["--time-slice-seconds", str(args.time_slice_seconds)])
    launcher_log.parent.mkdir(parents=True, exist_ok=True)
    with launcher_log.open("ab") as log:
        kwargs = {
            "cwd": str(root),
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, **kwargs)
    print(f"started Agent DevTools check pid={proc.pid} · log={launcher_log}")
    return 0

def command_status(root: Path) -> int:
    active = active_status(root)
    if active is None:
        print("no active Agent DevTools run")
        return 0
    print(json.dumps(active, ensure_ascii=False, indent=2))
    return 0


def command_cancel(root: Path) -> int:
    if cancel_active(root):
        print("cancellation signal sent")
        return 0
    print("no cancellable Agent DevTools run")
    return 1


def main(root: Path, args: argparse.Namespace) -> int:
    if args.check_command == "plan":
        return command_plan(root, args)
    if args.check_command == "run":
        return command_run(root, args)
    if args.check_command == "certify":
        return command_certify(root, args)
    if args.check_command == "replay":
        return command_replay(root, args)
    if args.check_command == "start":
        return command_start(root, args)
    if args.check_command == "status":
        return command_status(root)
    if args.check_command == "cancel":
        return command_cancel(root)
    raise AssertionError(args.check_command)
