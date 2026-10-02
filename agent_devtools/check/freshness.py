from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_devtools.core.files import FileInventory
from agent_devtools.core.hashing import fingerprint_paths, sha256_file
from agent_devtools.core.identity import engine_fingerprint
from agent_devtools.work.state import TaskStateError, load_task_state
from agent_devtools.work.verification import _load as load_verification

from .cache import StageCache
from .config import load_check_config, CheckConfigError
from .contracts import cached_outputs_match, missing_required
from .changes import git_changed_files
from .policy import load_policy, PolicyError
from .selection import apply_selection_safety_guards


def config_identity(root, config) -> str:
    return fingerprint_paths(root, [config.config_path or root / "agent-tools.json", config.policy_path])


def begin_binding(root, config) -> dict[str, Any]:
    task = load_task_state(root)
    path = config.config_path or root / "agent-tools.json"
    try:
        config_path = path.relative_to(root).as_posix()
    except ValueError:
        config_path = str(path)
    return {"version": 1, "taskId": (task or {}).get("taskId"),
            "engineFingerprint": engine_fingerprint(), "configFingerprint": config_identity(root, config),
            "configPath": config_path, "suites": {}}


def suite_key(root, config, plan, suite: str, input_hashes: dict[str, str]) -> str:
    from .runner import _resolved_execution, _execution_fingerprint
    spec = config.commands[suite]
    argv, cwd, _env, overrides = _resolved_execution(root, spec, plan)
    return StageCache.key(
        suite=suite, argv=argv, tool=spec.tool, input_hashes=input_hashes,
        execution=_execution_fingerprint(root, spec, cwd, overrides))


def validated_check_report(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Validate report integrity, coverage and inputs without executing commands."""
    from .runner import _file_set_paths
    records = load_verification(root)["records"]
    record = next((item for item in reversed(records) if item.get("label") == "check"), None)
    if not record or record.get("status") != "pass":
        raise TaskStateError("cannot complete work without a successful check; run work complete")
    try:
        path = (root / record["evidence"][0]).resolve()
        path.relative_to(root.resolve())
        if sha256_file(path) != record.get("reportSha256"):
            raise TaskStateError("check report integrity mismatch; run work complete")
        report = json.loads(path.read_text(encoding="utf-8"))
        binding = report.get("verificationBinding") or {}
        if report.get("status") != "pass" or not str(report.get("mode", "")).startswith("check-"):
            raise TaskStateError("verification is not a completed check run")
        if binding.get("version") != 1 or binding.get("taskId") != state.get("taskId"):
            raise TaskStateError("check is not bound to the current task; run work complete")
        if report.get("completedAtUtc", "") < state.get("startedAtUtc", ""):
            raise TaskStateError("verification is older than the current task")
        config = load_check_config(root, Path(binding["configPath"]))
        if (binding.get("engineFingerprint") != engine_fingerprint()
                or binding.get("configFingerprint") != config_identity(root, config)):
            raise TaskStateError("check engine/config is stale; run work complete")
        policy = load_policy(config.policy_path)
        selection = report["selection"]
        plan = apply_selection_safety_guards(policy.plan(selection["profile"], selection["changedFiles"]), config)
        changed = git_changed_files(root)
        current_profile = "affected" if "affected" in policy.profiles else selection["profile"]
        current_plan = apply_selection_safety_guards(policy.plan(current_profile,
            {"__unknown_current_change__"} if changed is None else changed | set(state.get("changedFiles", []))), config)
        required = set(config.suite_order) if changed is None else {item["id"] for item in current_plan.selected}
        current_groups = {item["id"] for item in current_plan.selected_application_groups}
        prior_groups = {item["id"] for item in plan.selected_application_groups}
        if not current_groups.issubset(prior_groups):
            raise TaskStateError("current changes exceed check group coverage; run work complete")
        covered = {item["id"] for item in plan.selected}
        if not required.issubset(covered):
            raise TaskStateError("current changes exceed check coverage; run work complete")
        inventory = FileInventory(root, config.ignore)
        stale = []
        for suite in sorted(covered):
            spec = config.commands[suite]
            files = _file_set_paths(root, spec.file_set, config.ignore) if spec.file_set else []
            result = (report.get("checks", {}).get(suite) or {}).get("result", {})
            if (binding["suites"].get(suite) != suite_key(root, config, plan, suite, inventory.hashes(spec.inputs, files))
                    or missing_required(root, spec.requires)
                    or not cached_outputs_match(root, spec.outputs, result)):
                stale.append(suite)
        if binding.get("globalInputs") is not None and binding["globalInputs"] != inventory.hashes(config.certification.global_inputs):
            stale.append("certification global inputs")
        if stale:
            raise TaskStateError("verification inputs/outputs are stale: " + ", ".join(stale) + "; run work complete")
        return report
    except TaskStateError:
        raise
    except (OSError, ValueError, KeyError, IndexError, TypeError, CheckConfigError, PolicyError) as exc:
        raise TaskStateError(f"cannot validate check evidence: {exc}; run work complete") from exc
