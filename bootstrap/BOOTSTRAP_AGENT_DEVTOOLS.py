#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

KIT_FORMAT = "agent-devtools-integration-update-kit"
KIT_VERSION = 10
PROJECT_SIGNALS = (
    ".git", "agent-tools.json", "agent-check.policy.json", "pyproject.toml", "requirements.txt", "pytest.ini",
    "setup.py", "setup.cfg", "package.json", "composer.json", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "src", "app", "tests",
)


def _looks_like_project(path: Path) -> bool:
    return path.is_dir() and any((path / name).exists() for name in PROJECT_SIGNALS)


def _infer_target(kit_root: Path, explicit: str | None) -> tuple[Path | None, list[str]]:
    if explicit:
        return Path(explicit).expanduser().resolve(), ["explicit --target"]
    cwd = Path.cwd().resolve()
    if cwd != kit_root and not str(cwd).startswith(str((kit_root / "payload").resolve())):
        if _looks_like_project(cwd):
            return cwd, ["current working directory has project signals"]
        return cwd, ["current working directory selected as promptless workspace root"]
    parent = kit_root.parent.resolve()
    if _looks_like_project(parent):
        return parent, ["kit is unpacked directly inside a project root"]
    candidates: list[Path] = []
    for base in (cwd, parent):
        if not base.is_dir():
            continue
        try:
            children = list(base.iterdir())
        except OSError:
            continue
        for child in children:
            if child == kit_root or not child.is_dir():
                continue
            if _looks_like_project(child):
                candidates.append(child.resolve())
    unique = sorted(set(candidates))
    if len(unique) == 1:
        return unique[0], ["exactly one adjacent directory has project signals"]
    if unique:
        return None, ["multiple possible project roots: " + ", ".join(str(p) for p in unique[:8])]
    return None, ["no unambiguous workspace root found near the kit"]


def _run(cmd: list[str], cwd: Path) -> dict:
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    return {"argv": cmd, "exitCode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "ok": proc.returncode == 0}


def _park_kit_if_inside_target(kit_root: Path, target: Path, report: dict) -> Path:
    try:
        rel = kit_root.relative_to(target)
        inside = True
    except ValueError:
        inside = False
        rel = None
    if not inside or (rel and ".agent-work" in rel.parts):
        return kit_root
    parked = target / ".agent-work" / "agent-devtools-bootstrap-kit"
    os.chdir(target)
    parked.parent.mkdir(parents=True, exist_ok=True)
    if parked.exists():
        shutil.rmtree(parked)
    shutil.move(str(kit_root), str(parked))
    report["parkedKit"] = str(parked)
    report["kitRoot"] = str(parked)
    return parked


def _post_checks(target: Path) -> list[dict]:
    py = sys.executable
    agent = target / "devtools" / "agent" / "agent.py"
    return [
        _run([py, str(agent), "doctor", "--json"], target),
        _run([py, str(agent), "context", "validate", "--json"], target),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent DevTools promptless integration/update bootstrap")
    parser.add_argument("--target", default=None, help="target project root; normally inferred")
    parser.add_argument("--preset", default=None, help="explicit development preset for a new/unconfigured project")
    parser.add_argument("--profile", choices=["development","research","analysis","document","general"], default=None, help="explicit work profile override; normally inferred from intent/project evidence")
    parser.add_argument("--intent", default=None, help="what the user plans to do or discuss in this workspace")
    parser.add_argument("--stack", default=None, help="development stack description when it cannot be inferred from an existing repository")
    parser.add_argument("--plan", action="store_true", help="show what would happen without changing the project")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    kit_root = Path(__file__).resolve().parent
    payload = kit_root / "payload"
    sys.path.insert(0, str(payload))
    try:
        from agent_devtools.bootstrap import (
            BootstrapError, apply_plan, build_plan, bootstrap_neutral_workspace, classify_intent, detect_stack, inspect_existing_installation, set_workspace_profile, specialize_development_workspace, upgrade_runtime,
        )
        from agent_devtools.presets import PresetError
    except Exception as exc:
        print(f"integration kit is damaged: {exc}", file=sys.stderr)
        return 3

    target, reasons = _infer_target(kit_root, args.target)
    report: dict = {
        "format": KIT_FORMAT,
        "formatVersion": KIT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "kitRoot": str(kit_root),
        "target": str(target) if target else None,
        "targetResolution": reasons,
        "requestedPreset": args.preset,
        "requestedProfile": args.profile,
        "requestedIntent": args.intent,
        "requestedStack": args.stack,
        "status": "planning",
    }
    if target is None:
        report.update({
            "status": "needs-target",
            "agentAction": "Infer the repository root from the workspace/session and rerun with --target <path>. Ask the human only if multiple unrelated repositories are genuinely plausible.",
        })
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    try:
        inspection = inspect_existing_installation(target)
        report["existingInstallation"] = inspection
        root_tools = target / "agent-tools.json"
        root_policy = target / "agent-check.policy.json"
        has_tools = root_tools.is_file()
        existing_profile = None
        if has_tools:
            try:
                existing_profile = str((json.loads(root_tools.read_text(encoding="utf-8")).get("work") or {}).get("profile") or "development")
            except Exception:
                existing_profile = "development"
        if has_tools and existing_profile == "development" and not root_policy.is_file():
            raise BootstrapError("Development profile requires agent-check.policy.json before update")

        stack_report = detect_stack(target)
        report["stackDetection"] = stack_report
        intent_text = args.intent
        intent_detection = classify_intent(intent_text, stack=args.stack) if intent_text else None
        if intent_detection:
            report["intentDetection"] = {
                "profile": intent_detection.profile_id, "development": intent_detection.development,
                "preset": intent_detection.preset_id, "confidence": intent_detection.confidence,
                "reasons": list(intent_detection.reasons), "needsStack": intent_detection.needs_stack,
            }

        if has_tools:
            report["mode"] = "update-existing"
            if args.plan:
                report["status"] = "plan-ready"
                report["agentAction"] = "Existing project configuration will be preserved byte-for-byte unless explicit specialization was requested; disposable runtime will be updated."
                print(json.dumps(report, ensure_ascii=False, indent=2) if args.json_output else _human(report))
                return 0
            result = upgrade_runtime(target, source_root=payload)
            report["update"] = result
            current_profile = result.get("profile")
            requested_dev_preset = args.preset or (intent_detection.preset_id if intent_detection and intent_detection.development else None)
            requested_neutral_profile = args.profile if args.profile in {"research", "analysis", "document", "general"} else None
            inferred_profile = None if args.profile else intent_detection
            if current_profile != "development" and args.profile == "development":
                if not requested_dev_preset:
                    report.update({
                        "status": "needs-stack",
                        "agentAction": "This neutral workspace is now known to be a software-development project, but the stack is not specific enough. Ask what language/framework/test/build tools are planned, then rerun the same bootstrap with --intent and --stack (or --preset).",
                    })
                    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json_output else _human(report)); return 0
                report["specialization"] = specialize_development_workspace(target, requested_dev_preset, source_root=payload)
            elif current_profile != "development" and requested_neutral_profile and requested_neutral_profile != current_profile:
                report["specialization"] = set_workspace_profile(target, requested_neutral_profile)
            elif current_profile != "development" and inferred_profile and inferred_profile.development:
                if not requested_dev_preset:
                    report.update({
                        "status": "needs-stack",
                        "agentAction": "This neutral workspace is now known to be a software-development project, but the stack is not specific enough. Ask what language/framework/test/build tools are planned, then rerun the same bootstrap with --intent and --stack (or --preset).",
                    })
                    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json_output else _human(report)); return 0
                report["specialization"] = specialize_development_workspace(target, requested_dev_preset, source_root=payload)
            elif current_profile != "development" and inferred_profile and inferred_profile.profile_id != current_profile:
                report["specialization"] = set_workspace_profile(target, inferred_profile.profile_id)
        else:
            report["mode"] = "bootstrap-new-or-unconfigured"
            plan = build_plan(target, preset_id=args.preset, source_root=payload)
            report["plan"] = plan
            existing_workspace = plan["mode"] == "existing"

            # Existing repositories are evidence-first: a high/medium safe preset is applied automatically.
            if existing_workspace and plan["readyToApply"]:
                if args.plan:
                    report["status"] = "plan-ready"
                    report["agentAction"] = "Existing repository stack was inferred from project evidence; the matching development preset will be applied automatically."
                    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json_output else _human(report)); return 0
                report["applied"] = apply_plan(target, preset_id=args.preset, source_root=payload, force=(plan["vendor"]["action"] == "replace-required"))
            elif existing_workspace and (plan["detection"]["confidence"] == "low" or stack_report.get("technologies")):
                report.update({
                    "status": "needs-development-adapter",
                    "agentAction": "An existing software stack was detected from repository evidence, so do not ask the human to identify the stack again. The detected stack is not yet safely mapped to a bundled preset; inspect project-native test/build commands and configure the development adapter/preset from that evidence, asking only for genuinely missing project decisions.",
                })
                print(json.dumps(report, ensure_ascii=False, indent=2) if args.json_output else _human(report)); return 0
            else:
                # Empty or non-development-looking workspace: always establish the universal neutral layer first.
                neutral_profile = args.profile if args.profile in {"research","analysis","document","general"} else "general"
                if args.plan:
                    report["status"] = "plan-ready"
                    report["agentAction"] = f"Universal non-development layer will be created first with profile={neutral_profile}; specialization follows only after intent/stack is known."
                    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json_output else _human(report)); return 0
                report["applied"] = bootstrap_neutral_workspace(target, profile_id=neutral_profile, source_root=payload)

                if intent_text is None and not args.json_output and sys.stdin.isatty():
                    intent_text = input("What are you planning to do or discuss in this project? ").strip() or None
                    intent_detection = classify_intent(intent_text, stack=args.stack) if intent_text else None
                    if intent_detection and intent_detection.development and intent_detection.needs_stack and not args.stack:
                        args.stack = input("What stack are you planning to use (language, framework, tests/build tools)? ").strip() or None
                        intent_detection = classify_intent(intent_text, stack=args.stack)
                    if intent_detection:
                        report["intentDetection"] = {"profile": intent_detection.profile_id, "development": intent_detection.development, "preset": intent_detection.preset_id, "confidence": intent_detection.confidence, "reasons": list(intent_detection.reasons), "needsStack": intent_detection.needs_stack}

                if intent_detection is None and args.profile is None:
                    report["status"] = "needs-intent"
                    report["agentAction"] = "Universal workspace is ready. Ask the human: ‘What are you planning to do or discuss in this project?’ Then rerun this same bootstrap with --intent <answer>. Do not ask them to choose an internal Agent DevTools profile."
                    checks = _post_checks(target); report["postChecks"] = checks
                    try: (target / ".agent-bootstrap-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    except OSError: pass
                    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json_output else _human(report)); return 0
                inferred_profile = None if args.profile else intent_detection
                if args.profile == "development" or (inferred_profile and inferred_profile.development):
                    preset = args.preset or (intent_detection.preset_id if intent_detection else None)
                    if not preset:
                        report["status"] = "needs-stack"
                        report["agentAction"] = "Development intent is clear, but the stack is not. Ask for language/framework/test/build tools, then rerun with --intent and --stack. The universal layer remains valid and must not be discarded."
                        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json_output else _human(report)); return 0
                    report["specialization"] = specialize_development_workspace(target, preset, source_root=payload)
                elif inferred_profile and inferred_profile.profile_id != neutral_profile:
                    report["specialization"] = set_workspace_profile(target, inferred_profile.profile_id)

        kit_root = _park_kit_if_inside_target(kit_root, target, report)
        checks = _post_checks(target)
        report["postChecks"] = checks
        report["status"] = "ready" if all(item["ok"] for item in checks) else "installed-check-failed"
        report["agentAction"] = (
            "Agent DevTools is ready. Keep AGENTS.md, agent-tools.json, agent-check.policy.json, project semantic maps, and .agent-knowledge in Git; devtools/agent, .agent-cache, and .agent-work stay disposable/ignored. Start or resume substantial work through the managed workflow contract in AGENTS.md; inspect workflow show/capabilities when needed."
            if report["status"] == "ready"
            else "Runtime/bootstrap completed but a post-check failed. Diagnose the recorded output before coding; do not silently downgrade or overwrite project configuration."
        )
        try:
            (target / ".agent-bootstrap-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json_output else _human(report))
        return 0 if report["status"] == "ready" else 4
    except (BootstrapError, PresetError) as exc:
        report.update({
            "status": "conflict-or-invalid",
            "error": str(exc),
            "agentAction": "Reconcile the technical conflict autonomously when safe. Preserve root project configuration and semantic maps; never solve an update conflict by blindly replacing them. Ask the human only for an actual product/configuration decision, not routine migration mechanics.",
        })
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2


def _human(report: dict) -> str:
    lines = [f"Agent DevTools integration/update: {report.get('status')}"]
    if report.get("target"):
        lines.append(f"target: {report['target']}")
    if report.get("mode"):
        lines.append(f"mode: {report['mode']}")
    update = report.get("update") or {}
    if update:
        lines.append(f"runtime: {update.get('action')} · config preserved: {update.get('projectConfigPreserved')}")
    applied = report.get("applied") or {}
    performed = applied.get("performed") or {}
    if performed:
        lines.append(f"toolkit: {performed.get('vendor')} · config: {performed.get('config')} · gitignore: {performed.get('gitignore')}")
    checks = report.get("postChecks") or []
    if checks:
        lines.append("post-checks: " + ", ".join("PASS" if c.get("ok") else "FAIL" for c in checks))
    if report.get("agentAction"):
        lines.append("next: " + report["agentAction"])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
