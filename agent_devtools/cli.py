from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from . import __version__
from .check import cli as check_cli
from . import changes_cli
from .bootstrap import BootstrapError, apply_plan as apply_bootstrap_plan, build_plan as build_bootstrap_plan
from .context import cli as context_cli
from .core.workspace import default_work_root
from .project import discover_project_root
from .presets import PresetError, apply_preset, get_preset, list_presets
from .onboarding import ensure as ensure_onboarding, status as onboarding_status
from .profiles import ProfileError, get_profile, list_profiles, load_profile, set_profile
from . import preserve as preserve_cli
from . import handoff_cli
from .release import cli as release_cli
from . import shell as shell_cli
from .self_update import SelfUpdateError, self_update as perform_self_update
from .work import cli as work_cli
from .workflow import capability_diff, capability_summary, capabilities as workflow_capabilities, workflow_contract
from . import workspace_snapshot_cli



_METRIC_DEPTH = 0


class _CountingStdout:
    def __init__(self, target) -> None:
        self._target = target
        self.bytes_written = 0

    def write(self, value: str):
        self.bytes_written += len(str(value).encode("utf-8"))
        return self._target.write(value)

    def flush(self):
        return self._target.flush()

    def __getattr__(self, name):
        return getattr(self._target, name)


def _metric_command(raw_argv: list[str]) -> str:
    if not raw_argv:
        return ""
    first = str(raw_argv[0])
    if len(raw_argv) > 1 and not str(raw_argv[1]).startswith("-") and first in {
        "workflow", "profile", "onboarding", "changes", "check", "release", "context",
        "source", "verify", "cognition", "knowledge", "work", "task", "handoff",
        "preserve", "checkpoint", "workspace-snapshot", "preset",
    }:
        return first + " " + str(raw_argv[1])
    return first


def _record_cli_metric(
    root: Path,
    *,
    command: str,
    duration_ms: float,
    stdout_bytes: int,
    exit_code: int,
) -> None:
    try:
        path = default_work_root(root) / "cli-overhead.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "format": "agent-devtools-cli-overhead",
            "formatVersion": 1,
            "toolVersion": __version__,
            "command": command,
            "durationMs": round(float(duration_ms), 3),
            "stdoutBytes": int(stdout_bytes),
            "exitCode": int(exit_code),
            "recordedAtUnixNs": time.time_ns(),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except (OSError, ValueError, TypeError):
        # Passive instrumentation must never make the user's actual command fail.
        return


def _fts5_available() -> bool:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts_probe USING fts5(content)")
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def _doctor(as_json: bool = False) -> int:
    root = discover_project_root()
    payload = {
        "toolVersion": __version__,
        "projectRoot": str(root),
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "fts5": _fts5_available(),
        "git": shutil.which("git") is not None,
        "config": str(root / "agent-tools.json") if (root / "agent-tools.json").is_file() else None,
        "cacheDir": str(root / ".agent-cache"),
        "status": "ready",
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Agent DevTools {payload['toolVersion']}")
        print(f"project: {payload['projectRoot']}")
        print(f"python: {payload['python']}")
        print(f"sqlite: {payload['sqlite']} (FTS5: {'yes' if payload['fts5'] else 'no'})")
        print(f"git: {'yes' if payload['git'] else 'no'}")
        print(f"config: {payload['config'] or 'auto-discovery'}")
        try:
            profile = load_profile(root)
            print(f"profile: {profile.profile_id} · verify={profile.verification_mode}")
        except ProfileError:
            pass
        print("stage: Stage L non-development bootstrap + workspace snapshot")
    return 0


def _not_implemented(name: str) -> int:
    print(f"agent {name}: not implemented yet; see docs/ROADMAP.md", file=sys.stderr)
    return 2



def _command_inventory(cli_parser: argparse.ArgumentParser) -> tuple[str, ...]:
    commands: set[str] = set()

    def walk(current: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None:
        for action in current._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for name, child in action.choices.items():
                path = (*prefix, str(name))
                commands.add(" ".join(path))
                walk(child, path)

    walk(cli_parser, ())
    return tuple(sorted(commands))


def _validate_workflow_commands(
    contract: dict[str, object],
    cli_commands: tuple[str, ...] | list[str],
) -> dict[str, object]:
    available = set(cli_commands)
    advertised = sorted({
        str(command)
        for phase in contract.get("phases", [])
        if isinstance(phase, dict)
        for command in phase.get("commands", [])
        if str(command).strip()
    })
    missing = [command for command in advertised if command not in available]
    return {
        "format": "agent-devtools-workflow-validation",
        "formatVersion": 1,
        "status": "pass" if not missing else "fail",
        "advertised": len(advertised),
        "available": len(available),
        "missing": missing,
    }


def _capabilities_payload(root: Path, cli_parser: argparse.ArgumentParser) -> dict[str, object]:
    payload = workflow_capabilities(root)
    payload["cliCommands"] = list(_command_inventory(cli_parser))
    return payload



def _natural_guess_hint(argv: list[str]) -> str | None:
    if len(argv) >= 2 and argv[0] == "cognition" and argv[1] == "add":
        return (
            "agent cognition add: `add` is not a semantic type. "
            "Choose observation | finding | assumption | decision | requirement | open-question | evidence | blocker. "
            "For general notes use `agent cognition observation --stdin` or `agent cognition observation --from-file <path>`."
        )
    return None


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent", description="Portable agent work toolbox")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("bootstrap", help="plan/apply Agent DevTools into another project")
    bootstrap.add_argument("target", type=Path, help="existing or new project directory")
    bootstrap.add_argument("--preset", default=None, help="explicit bundled preset; omit for safe autodetect")
    bootstrap.add_argument("--apply", action="store_true", help="write toolkit/config; default is read-only plan")
    bootstrap.add_argument("--force", action="store_true", help="replace differing existing toolkit/config")
    bootstrap.add_argument("--json", action="store_true", dest="json_output")
    profile = sub.add_parser("profile", help="select the neutral work profile used by Agent DevTools Core")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("list", help="list built-in work profiles")
    profile_show = profile_sub.add_parser("show", help="show the active work profile")
    profile_show.add_argument("--json", action="store_true", dest="json_output")
    profile_set = profile_sub.add_parser("set", help="set the tracked project work profile")
    profile_set.add_argument("profile_id")
    profile_set.add_argument("--json", action="store_true", dest="json_output")
    workflow = sub.add_parser("workflow", help="show the stable Agent DevTools work contract")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_show = workflow_sub.add_parser("show", help="show the mandatory workflow for the active profile")
    workflow_show.add_argument("--json", action="store_true", dest="json_output")
    workflow_show.add_argument("--details", action="store_true", help="include all workflow responsibilities and commands")
    workflow_validate = workflow_sub.add_parser("validate", help="verify advertised workflow commands against the real CLI")
    workflow_validate.add_argument("--json", action="store_true", dest="json_output")
    capabilities = sub.add_parser("capabilities", help="capability discovery; compact summary/diff or full machine-readable contract")
    capability_view = capabilities.add_mutually_exclusive_group()
    capability_view.add_argument("--summary", action="store_true", help="show the compact routine-focused capability summary")
    capability_view.add_argument("--diff", dest="diff_version", metavar="VERSION", help="show semantic capability changes since a bundled prior version")
    capabilities.add_argument("--json", action="store_true", dest="json_output")
    onboarding = sub.add_parser("onboarding", help="shared cross-agent project onboarding contract")
    onboarding_sub = onboarding.add_subparsers(dest="onboarding_command", required=True)
    onboarding_ensure = onboarding_sub.add_parser("ensure", help="create/update the managed Agent DevTools block in AGENTS.md")
    onboarding_ensure.add_argument("--json", action="store_true", dest="json_output")
    onboarding_show = onboarding_sub.add_parser("status", help="show AGENTS.md onboarding state")
    onboarding_show.add_argument("--json", action="store_true", dest="json_output")
    doctor = sub.add_parser("doctor", help="check the local zero-dependency runtime")
    doctor.add_argument("--json", action="store_true")
    self_update = sub.add_parser("self-update", help="freshly discover/download/verify and install Agent DevTools")
    self_update.add_argument("--version", help="explicit target release; omit to follow canonical handoff")
    self_update.add_argument("--check", action="store_true", help="read-only update availability check")
    self_update.add_argument("--timeout", type=float, default=30.0, help="network timeout in seconds")
    self_update.add_argument("--json", action="store_true", dest="json_output")
    changes = sub.add_parser("changes", help="canonical project change-set and patch discovery")
    changes_cli.configure_parser(changes)
    check = sub.add_parser("check", help="portable verification facade")
    check_cli.configure_parser(check)
    preset = sub.add_parser("preset", help="inspect/apply declarative project presets")
    preset_sub = preset.add_subparsers(dest="preset_command", required=True)
    preset_sub.add_parser("list", help="list bundled presets")
    preset_show = preset_sub.add_parser("show", help="show one bundled preset")
    preset_show.add_argument("preset_id")
    preset_apply = preset_sub.add_parser("apply", help="write preset config into the current project")
    preset_apply.add_argument("preset_id")
    preset_apply.add_argument("--force", action="store_true", help="overwrite existing preset-managed files")
    release = sub.add_parser("release", help="portable release packaging facade")
    release_cli.configure_parser(release)
    context = sub.add_parser("context", help="local repository context/retrieval facade")
    context_cli.configure_parser(context)
    begin = sub.add_parser("begin", help="canonical Agent Work Lifecycle entry: enter/resume work and immediately project durable context")
    work_cli.configure_begin_parser(begin)
    shell = sub.add_parser("shell", help="interactive thin UX over the ordinary Agent DevTools CLI")
    shell_cli.configure_parser(shell, command_dest="shell_commands")
    source = sub.add_parser("source", help="research/document source provenance")
    work_cli.configure_source_parser(source)
    verify = sub.add_parser("verify", help="profile-neutral verification evidence")
    work_cli.configure_verify_parser(verify)
    cognition = sub.add_parser("cognition", help="structured lightweight session cognition")
    work_cli.configure_cognition_parser(cognition)
    knowledge = sub.add_parser("knowledge", help="tracked durable project knowledge")
    work_cli.configure_knowledge_parser(knowledge)
    work = sub.add_parser("work", help="lightweight session cognition and work harness")
    work_cli.configure_work_parser(work)
    task = sub.add_parser("task", help="tiny local task-continuity state")
    work_cli.configure_task_parser(task)
    brief = sub.add_parser("brief", help="assemble a compact current-work briefing")
    work_cli.configure_brief_parser(brief)
    resume = sub.add_parser("resume", help="assemble a timeout/session-recovery briefing")
    work_cli.configure_brief_parser(resume)
    handoff = sub.add_parser("handoff", help="portable cross-session/cross-agent handoff")
    handoff_cli.configure_parser(handoff)
    preserve = sub.add_parser("preserve", help="profile-aware portable work preservation")
    preserve_cli.configure_parser(preserve)
    checkpoint = sub.add_parser("checkpoint", help="portable unfinished-work recovery bundles")
    work_cli.configure_checkpoint_parser(checkpoint)
    workspace_snapshot = sub.add_parser("workspace-snapshot", help="portable snapshots for non-development workspaces")
    workspace_snapshot_cli.configure_parser(workspace_snapshot)
    return p


def _main_impl(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    hint = _natural_guess_hint(raw_argv)
    if hint:
        print(hint, file=sys.stderr)
        return 2
    cli_parser = parser()
    args = cli_parser.parse_args(raw_argv)
    if args.command == "bootstrap":
        try:
            payload = (
                apply_bootstrap_plan(args.target, preset_id=args.preset, force=bool(args.force))
                if args.apply
                else build_bootstrap_plan(args.target, preset_id=args.preset)
            )
        except (BootstrapError, PresetError) as exc:
            print(f"agent bootstrap: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            action = "APPLY" if args.apply else "PLAN"
            print(f"{action}: {payload['target']} · mode={payload['mode']}")
            detection = payload['detection']
            print(f"  preset: {payload['selectedPreset'] or 'choice required'} · detect={detection['confidence']}")
            for reason in detection['reasons']:
                print(f"    - {reason}")
            vendor_action = payload.get("performed", {}).get("vendor", payload['vendor']['action'])
            config_action = payload.get("performed", {}).get("config", payload['config']['action'])
            print(f"  toolkit: {vendor_action} -> {payload['vendor']['path']} ({payload['vendor']['sourceFiles']} files)")
            print(f"  config: {config_action} -> {', '.join(payload['config']['files']) or 'not selected'}")
            if payload.get('pythonSourceRoots'):
                print("  python roots: " + ", ".join(payload['pythonSourceRoots']))
            if not payload['readyToApply']:
                print("  next: choose --preset from " + ", ".join(detection['candidates']))
            elif not args.apply:
                print("  next: rerun with --apply after reviewing this plan")
            else:
                print("  verified: config + policy parse; consumer checks were not run")
        return 0 if payload['readyToApply'] or args.preset else 2
    if args.command == "profile":
        root = discover_project_root()
        try:
            if args.profile_command == "list":
                for item in list_profiles():
                    print(f"{item.profile_id}: {item.title} — {item.description}")
                return 0
            item = set_profile(root, args.profile_id) if args.profile_command == "set" else load_profile(root)
        except ProfileError as exc:
            print(f"agent profile: {exc}", file=sys.stderr)
            return 2
        payload = {"id": item.profile_id, "title": item.title, "description": item.description, "development": item.development, "verificationMode": item.verification_mode, "changeDiscovery": item.change_discovery}
        if getattr(args, "json_output", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"profile: {item.profile_id} · {item.title} · verify={item.verification_mode}")
        return 0
    if args.command == "workflow":
        root = discover_project_root()
        contract = workflow_contract(root)
        if args.workflow_command == "validate":
            payload = _validate_workflow_commands(contract, _command_inventory(cli_parser))
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(
                    f"workflow validation: {payload['status'].upper()} · "
                    f"advertised={payload['advertised']} · available={payload['available']}"
                )
                for missing in payload["missing"]:
                    print(f"  missing: {missing}")
            return 0 if payload["status"] == "pass" else 1
        payload = contract
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"workflow contract v{payload['formatVersion']} · profile={payload['profile']['id']}")
            route = payload.get("routineRoute")
            if route:
                routine = route.get("canonicalRoutine") or [route["entry"], "work", "checkpoint", "complete"]
                print("  routine: " + " -> ".join(routine))
                surface = route.get("surface") or {}
                if surface:
                    print("  normal surface: " + " | ".join(surface.get("normalCommands", [])))
                print("  completion: reuse current PASS; otherwise run required checks")
                if route.get("toolFailureRecovery"):
                    print("  failure recovery: two equivalent failures -> fallback/skip optional work or block mandatory work")
                print("  gates: task alignment, blockers, verification and knowledge consistency")
                print("  context indexing and knowledge promotion: optional")
                print("  deeper tools: certification, replay and release when explicitly needed")
                if not args.details:
                    print("  details: workflow show --details (or --json)")
            if args.details:
                print("  responsibilities (not separate mandatory calls):")
                for phase in payload["phases"]:
                    marker = "required" if phase["required"] else "optional"
                    print(f"  {phase['id']}: {marker} · {phase['purpose']}")
                    print("    commands: " + ", ".join(phase["commands"]))
        return 0
    if args.command == "capabilities":
        root = discover_project_root()
        try:
            if args.diff_version:
                payload = capability_diff(root, args.diff_version)
            elif args.summary:
                payload = capability_summary(root)
            else:
                payload = _capabilities_payload(root, cli_parser)
        except ValueError as exc:
            print(f"agent capabilities: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.diff_version:
            cli_contract = payload["contracts"]["cli"]
            workflow_contract_delta = payload["contracts"]["workflow"]
            print(f"capabilities diff {payload['fromVersion']} -> {payload['toVersion']}")
            print(f"  contracts: CLI {cli_contract['from']} -> {cli_contract['to']} · workflow {workflow_contract_delta['from']} -> {workflow_contract_delta['to']}")
            routine_changes = payload["routineAdded"] + payload["routineRemoved"]
            print("  routine: " + ("changed" if routine_changes else "unchanged"))
            diagnostic_changes = payload["diagnosticsAdded"] + payload["diagnosticsRemoved"]
            print("  diagnostics: " + ("changed" if diagnostic_changes else "unchanged"))
            print("  new: " + (", ".join(payload["newCapabilities"]) or "none"))
            print("  migration: " + (", ".join(payload["migrationWarnings"]) or "none"))
        elif args.summary:
            print(f"Agent DevTools {payload['toolVersion']} · CLI v{payload['cliContractVersion']} · workflow v{payload['workflowContractVersion']} · profile={payload['profile']}")
            print("  routine: " + " | ".join(payload["routineCommands"]))
            print("  diagnostics: " + " | ".join(payload["diagnostics"]) + " (pull-only)")
            print("  release changes: " + (", ".join(payload["releaseChanges"]) or "none"))
            print("  migration: " + (", ".join(payload["migrationWarnings"]) or "none"))
        else:
            print(f"Agent DevTools {payload['toolVersion']} · CLI contract v{payload['cliContractVersion']} · profile={payload['profile']['profile_id']}")
            print("capabilities: use --summary for the compact view, --diff VERSION for semantic changes, or --json for the full contract")
        return 0
    if args.command == "onboarding":
        root = discover_project_root()
        if args.onboarding_command == "ensure":
            performed = ensure_onboarding(root)
            payload = onboarding_status(root)
            payload["performed"] = performed
        else:
            payload = onboarding_status(root)
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"onboarding: {payload['path']} · {payload.get('performed', payload['action'])}")
        return 0
    if args.command == "doctor":
        return _doctor(args.json)
    if args.command == "self-update":
        root = discover_project_root()
        try:
            payload = perform_self_update(root, requested_version=args.version, check_only=bool(args.check), timeout=float(args.timeout))
        except SelfUpdateError as exc:
            print(f"agent self-update: {exc}", file=sys.stderr)
            return 2
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"self-update: {payload['status']} · {payload['currentVersion']} -> {payload['targetVersion']}")
            if payload.get("performed"):
                print(f"  installed: {payload.get('installedVersion')}")
        return 0
    if args.command == "changes":
        return changes_cli.main(discover_project_root(), args)
    if args.command == "check":
        return check_cli.main(discover_project_root(), args)
    if args.command == "release":
        return release_cli.main(discover_project_root(), args)
    if args.command == "context":
        return context_cli.main(discover_project_root(), args)
    if args.command == "begin":
        return work_cli.main_begin(discover_project_root(), args)
    if args.command == "shell":
        return shell_cli.main(discover_project_root(), args, dispatcher=main)
    if args.command == "source":
        return work_cli.main_source(discover_project_root(), args)
    if args.command == "verify":
        return work_cli.main_verify(discover_project_root(), args)
    if args.command == "cognition":
        return work_cli.main_cognition(discover_project_root(), args)
    if args.command == "knowledge":
        return work_cli.main_knowledge(discover_project_root(), args)
    if args.command == "work":
        return work_cli.main_work(discover_project_root(), args)
    if args.command == "task":
        return work_cli.main_task(discover_project_root(), args)
    if args.command == "brief":
        return work_cli.main_brief(discover_project_root(), args, mode="brief")
    if args.command == "resume":
        return work_cli.main_brief(discover_project_root(), args, mode="resume")
    if args.command == "handoff":
        return handoff_cli.main(discover_project_root(), args)
    if args.command == "preserve":
        return preserve_cli.main(discover_project_root(), args)
    if args.command == "checkpoint":
        return work_cli.main_checkpoint(discover_project_root(), args)
    if args.command == "workspace-snapshot":
        return workspace_snapshot_cli.main(discover_project_root(), args)
    if args.command == "preset":
        try:
            if args.preset_command == "list":
                for item in list_presets():
                    suffix = f" — {item.description}" if item.description else ""
                    print(f"{item.preset_id}: {item.title}{suffix}")
                return 0
            if args.preset_command == "show":
                item = get_preset(args.preset_id)
                print(json.dumps({
                    "id": item.preset_id,
                    "title": item.title,
                    "description": item.description,
                    "requirements": list(item.requirements),
                    "components": list(item.components),
                    "files": item.files,
                }, ensure_ascii=False, indent=2))
                return 0
            if args.preset_command == "apply":
                root = discover_project_root()
                item = get_preset(args.preset_id)
                written = apply_preset(item, root, force=args.force)
                print(f"applied preset {item.preset_id}")
                for path in written:
                    print(f"  {path.relative_to(root)}")
                return 0
        except PresetError as exc:
            print(f"agent preset: {exc}", file=sys.stderr)
            return 2
    return _not_implemented(args.command)


def main(argv: list[str] | None = None) -> int:
    global _METRIC_DEPTH
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if _METRIC_DEPTH:
        return _main_impl(raw_argv)

    target = sys.stdout
    counted = _CountingStdout(target)
    started = time.perf_counter()
    exit_code = 2
    _METRIC_DEPTH += 1
    sys.stdout = counted
    try:
        exit_code = _main_impl(raw_argv)
        return exit_code
    finally:
        sys.stdout = target
        _METRIC_DEPTH -= 1
        try:
            root = discover_project_root()
        except Exception:
            root = None
        if root is not None:
            _record_cli_metric(
                root,
                command=_metric_command(raw_argv),
                duration_ms=(time.perf_counter() - started) * 1000.0,
                stdout_bytes=counted.bytes_written,
                exit_code=exit_code,
            )
