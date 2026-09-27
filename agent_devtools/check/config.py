from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import SUPPORTED_ADAPTERS


class CheckConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class GuardRule:
    suite: str
    patterns: tuple[str, ...]
    select_all_application_groups: bool = False


@dataclass(frozen=True)
class DependencyRule:
    if_selected_any: tuple[str, ...]
    require: str
    reason: str


@dataclass(frozen=True)
class ResultAdapterSpec:
    kind: str = "exit-code"
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.options}


@dataclass(frozen=True)
class FileSetSpec:
    patterns: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    allow_empty: bool = True
    per_file_timeout_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "patterns": list(self.patterns),
            "exclude": list(self.exclude),
            "allowEmpty": self.allow_empty,
            "perFileTimeoutSeconds": self.per_file_timeout_seconds,
        }


@dataclass(frozen=True)
class OutputSpec:
    required: tuple[str, ...] = ()
    capture: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"required": list(self.required), "capture": list(self.capture)}


@dataclass(frozen=True)
class ChunkCommand:
    id: str
    argv: tuple[str, ...]
    timeout_seconds: float | None = None
    idle_timeout_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "argv": list(self.argv),
            "timeoutSeconds": self.timeout_seconds,
            "idleTimeoutSeconds": self.idle_timeout_seconds,
        }


@dataclass(frozen=True)
class SuiteCommand:
    suite: str
    argv: tuple[str, ...]
    inputs: tuple[str, ...]
    tool: str | None = None
    timeout_seconds: float | None = None
    cache: bool = True
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    adapter: ResultAdapterSpec = field(default_factory=ResultAdapterSpec)
    requires: tuple[str, ...] = ()
    file_set: FileSetSpec | None = None
    outputs: OutputSpec = field(default_factory=OutputSpec)
    chunks: tuple[ChunkCommand, ...] = ()
    idle_timeout_seconds: float | None = None




@dataclass(frozen=True)
class CertificationConfig:
    profile: str = "full"
    reusable_suites: tuple[str, ...] = ()
    global_inputs: tuple[str, ...] = ()
    evidence_path: str = ".agent-cache/check-certified-evidence-v1.json"


@dataclass(frozen=True)
class ReplayConfig:
    source_include: tuple[str, ...] = ("**",)
    source_exclude: tuple[str, ...] = ()
    shared_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckConfig:
    root: Path
    policy_path: Path
    suite_order: tuple[str, ...]
    guards: tuple[GuardRule, ...]
    dependencies: tuple[DependencyRule, ...]
    commands: dict[str, SuiteCommand] = field(default_factory=dict)
    ignore: tuple[str, ...] = ()
    certification: CertificationConfig = field(default_factory=CertificationConfig)
    replay: ReplayConfig = field(default_factory=ReplayConfig)


def _string_list(value: Any, field: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise CheckConfigError(f"{field} must be {'a non-empty' if not allow_empty else 'an'} array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CheckConfigError(f"{field} must contain non-empty strings")
        result.append(item.strip())
    if len(result) != len(set(result)):
        raise CheckConfigError(f"{field} contains duplicates")
    return tuple(result)


def _positive_seconds(value: Any, field: str) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise CheckConfigError(f"{field} must be numeric") from exc
    if seconds <= 0:
        raise CheckConfigError(f"{field} must be positive")
    return seconds


def load_check_config(root: Path, config_path: Path | None = None) -> CheckConfig:
    root = root.resolve()
    if config_path is None:
        path = (root / "agent-tools.json").resolve()
    else:
        path = config_path if config_path.is_absolute() else (root / config_path)
        path = path.resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CheckConfigError(f"Agent DevTools config not found: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckConfigError(f"Agent DevTools config is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise CheckConfigError("Agent DevTools config root must be an object")
    check = raw.get("check")
    if not isinstance(check, dict):
        raise CheckConfigError("check must be an object")
    policy_rel = str(check.get("policy") or "").strip()
    if not policy_rel:
        raise CheckConfigError("check.policy is required")
    suite_order = _string_list(check.get("suiteOrder"), "check.suiteOrder", allow_empty=False)
    ignore = _string_list(raw.get("ignore", []), "ignore")

    guards: list[GuardRule] = []
    raw_guards = check.get("guards", [])
    if not isinstance(raw_guards, list):
        raise CheckConfigError("check.guards must be an array")
    for index, item in enumerate(raw_guards):
        if not isinstance(item, dict):
            raise CheckConfigError(f"check.guards[{index}] must be an object")
        suite = str(item.get("suite") or "").strip()
        if suite not in suite_order:
            raise CheckConfigError(f"check.guards[{index}].suite is not in suiteOrder: {suite!r}")
        patterns = _string_list(item.get("patterns"), f"check.guards[{index}].patterns", allow_empty=False)
        guards.append(GuardRule(
            suite=suite,
            patterns=patterns,
            select_all_application_groups=bool(item.get("selectAllApplicationGroups", False)),
        ))

    dependencies: list[DependencyRule] = []
    raw_dependencies = check.get("dependencies", [])
    if not isinstance(raw_dependencies, list):
        raise CheckConfigError("check.dependencies must be an array")
    for index, item in enumerate(raw_dependencies):
        if not isinstance(item, dict):
            raise CheckConfigError(f"check.dependencies[{index}] must be an object")
        if_selected_any = _string_list(
            item.get("ifSelectedAny"),
            f"check.dependencies[{index}].ifSelectedAny",
            allow_empty=False,
        )
        require = str(item.get("require") or "").strip()
        if require not in suite_order:
            raise CheckConfigError(f"check.dependencies[{index}].require is not in suiteOrder: {require!r}")
        unknown = sorted(set(if_selected_any) - set(suite_order))
        if unknown:
            raise CheckConfigError(f"check.dependencies[{index}] references suites outside suiteOrder: {', '.join(unknown)}")
        dependencies.append(DependencyRule(
            if_selected_any=if_selected_any,
            require=require,
            reason=str(item.get("reason") or "safety guard: selected verification requires dependency"),
        ))

    commands: dict[str, SuiteCommand] = {}
    raw_commands = check.get("commands", {})
    if not isinstance(raw_commands, dict):
        raise CheckConfigError("check.commands must be an object")
    for suite, item in raw_commands.items():
        if suite not in suite_order:
            raise CheckConfigError(f"check.commands declares suite outside suiteOrder: {suite!r}")
        if not isinstance(item, dict):
            raise CheckConfigError(f"check.commands.{suite} must be an object")
        argv = _string_list(item.get("argv"), f"check.commands.{suite}.argv", allow_empty=False)
        inputs = _string_list(item.get("inputs", []), f"check.commands.{suite}.inputs")
        timeout_seconds = _positive_seconds(item.get("timeoutSeconds"), f"check.commands.{suite}.timeoutSeconds")
        tool_value = item.get("tool")
        tool = None if tool_value is None else str(tool_value).strip() or None
        cwd_value = item.get("cwd")
        cwd = None if cwd_value is None else str(cwd_value).strip() or None
        if cwd is not None and Path(cwd).is_absolute():
            raise CheckConfigError(f"check.commands.{suite}.cwd must be project-relative")

        raw_env = item.get("env", {})
        if not isinstance(raw_env, dict):
            raise CheckConfigError(f"check.commands.{suite}.env must be an object")
        env: dict[str, str] = {}
        for key, value in raw_env.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
                raise CheckConfigError(f"check.commands.{suite}.env must contain string keys/values")
            env[key.strip()] = value

        raw_adapter = item.get("resultAdapter", "exit-code")
        if isinstance(raw_adapter, str):
            adapter_kind = raw_adapter.strip() or "exit-code"
            adapter_options: dict[str, Any] = {}
        elif isinstance(raw_adapter, dict):
            adapter_kind = str(raw_adapter.get("kind") or "exit-code").strip()
            adapter_options = {str(k): v for k, v in raw_adapter.items() if k != "kind"}
        else:
            raise CheckConfigError(f"check.commands.{suite}.resultAdapter must be a string or object")
        if adapter_kind not in SUPPORTED_ADAPTERS:
            raise CheckConfigError(
                f"check.commands.{suite}.resultAdapter has unsupported kind {adapter_kind!r}; "
                f"supported: {', '.join(sorted(SUPPORTED_ADAPTERS))}"
            )
        if adapter_kind == "vite" and "requiredFiles" in adapter_options:
            _string_list(adapter_options["requiredFiles"], f"check.commands.{suite}.resultAdapter.requiredFiles")
        if adapter_kind == "json-line":
            if "allowedFailures" in adapter_options:
                _string_list(adapter_options["allowedFailures"], f"check.commands.{suite}.resultAdapter.allowedFailures")
            for regex_field in ("normalCompletionRegex", "fatalRegex"):
                if regex_field not in adapter_options:
                    continue
                value = adapter_options[regex_field]
                if not isinstance(value, str):
                    raise CheckConfigError(f"check.commands.{suite}.resultAdapter.{regex_field} must be a string")
                try:
                    re.compile(value)
                except re.error as exc:
                    raise CheckConfigError(
                        f"check.commands.{suite}.resultAdapter.{regex_field} is invalid: {exc}"
                    ) from exc

        requires = _string_list(item.get("requires", []), f"check.commands.{suite}.requires")

        file_set: FileSetSpec | None = None
        raw_file_set = item.get("fileSet")
        if raw_file_set is not None:
            if not isinstance(raw_file_set, dict):
                raise CheckConfigError(f"check.commands.{suite}.fileSet must be an object")
            patterns = _string_list(
                raw_file_set.get("patterns"),
                f"check.commands.{suite}.fileSet.patterns",
                allow_empty=False,
            )
            exclude = _string_list(raw_file_set.get("exclude", []), f"check.commands.{suite}.fileSet.exclude")
            per_file_timeout = _positive_seconds(
                raw_file_set.get("perFileTimeoutSeconds"),
                f"check.commands.{suite}.fileSet.perFileTimeoutSeconds",
            )
            if not any(("{file}" in token or "{file_abs}" in token) for token in argv):
                raise CheckConfigError(f"check.commands.{suite}.fileSet requires {{file}} or {{file_abs}} in argv")
            file_set = FileSetSpec(
                patterns=patterns,
                exclude=exclude,
                allow_empty=bool(raw_file_set.get("allowEmpty", True)),
                per_file_timeout_seconds=per_file_timeout,
            )

        idle_timeout_seconds = _positive_seconds(
            item.get("idleTimeoutSeconds"),
            f"check.commands.{suite}.idleTimeoutSeconds",
        )

        chunks: list[ChunkCommand] = []
        raw_chunks = item.get("chunks", [])
        if not isinstance(raw_chunks, list):
            raise CheckConfigError(f"check.commands.{suite}.chunks must be an array")
        seen_chunk_ids: set[str] = set()
        for chunk_index, raw_chunk in enumerate(raw_chunks):
            if not isinstance(raw_chunk, dict):
                raise CheckConfigError(f"check.commands.{suite}.chunks[{chunk_index}] must be an object")
            chunk_id = str(raw_chunk.get("id") or "").strip()
            if not chunk_id:
                raise CheckConfigError(f"check.commands.{suite}.chunks[{chunk_index}].id is required")
            if chunk_id in seen_chunk_ids:
                raise CheckConfigError(f"check.commands.{suite}.chunks contains duplicate id {chunk_id!r}")
            seen_chunk_ids.add(chunk_id)
            chunk_argv = _string_list(
                raw_chunk.get("argv"),
                f"check.commands.{suite}.chunks[{chunk_index}].argv",
                allow_empty=False,
            )
            chunks.append(ChunkCommand(
                id=chunk_id,
                argv=chunk_argv,
                timeout_seconds=_positive_seconds(
                    raw_chunk.get("timeoutSeconds"),
                    f"check.commands.{suite}.chunks[{chunk_index}].timeoutSeconds",
                ),
                idle_timeout_seconds=_positive_seconds(
                    raw_chunk.get("idleTimeoutSeconds"),
                    f"check.commands.{suite}.chunks[{chunk_index}].idleTimeoutSeconds",
                ),
            ))
        if chunks and file_set is not None:
            raise CheckConfigError(f"check.commands.{suite} cannot combine chunks with fileSet")

        raw_outputs = item.get("outputs", {})
        if not isinstance(raw_outputs, dict):
            raise CheckConfigError(f"check.commands.{suite}.outputs must be an object")
        outputs = OutputSpec(
            required=_string_list(raw_outputs.get("required", []), f"check.commands.{suite}.outputs.required"),
            capture=_string_list(raw_outputs.get("capture", []), f"check.commands.{suite}.outputs.capture"),
        )

        commands[suite] = SuiteCommand(
            suite=suite,
            argv=argv,
            inputs=inputs,
            tool=tool,
            timeout_seconds=timeout_seconds,
            cache=bool(item.get("cache", True)),
            cwd=cwd,
            env=env,
            adapter=ResultAdapterSpec(kind=adapter_kind, options=adapter_options),
            requires=requires,
            file_set=file_set,
            outputs=outputs,
            chunks=tuple(chunks),
            idle_timeout_seconds=idle_timeout_seconds,
        )

    raw_certification = check.get("certification", {})
    if not isinstance(raw_certification, dict):
        raise CheckConfigError("check.certification must be an object")
    certification_profile = str(raw_certification.get("profile") or "full").strip() or "full"
    reusable_suites = _string_list(
        raw_certification.get("reusableSuites", []),
        "check.certification.reusableSuites",
    )
    unknown_reusable = sorted(set(reusable_suites) - set(suite_order))
    if unknown_reusable:
        raise CheckConfigError(
            "check.certification.reusableSuites references suites outside suiteOrder: "
            + ", ".join(unknown_reusable)
        )
    global_inputs = _string_list(
        raw_certification.get("globalInputs", []),
        "check.certification.globalInputs",
    )
    evidence_path = str(
        raw_certification.get("evidencePath") or ".agent-cache/check-certified-evidence-v1.json"
    ).strip()
    if not evidence_path:
        raise CheckConfigError("check.certification.evidencePath must be a non-empty project-relative path")
    evidence = Path(evidence_path)
    if evidence.is_absolute() or ".." in evidence.parts:
        raise CheckConfigError("check.certification.evidencePath must stay inside the project root")

    raw_replay = check.get("replay", {})
    if not isinstance(raw_replay, dict):
        raise CheckConfigError("check.replay must be an object")
    source_include = _string_list(
        raw_replay.get("sourceInclude", ["**"]),
        "check.replay.sourceInclude",
        allow_empty=False,
    )
    source_exclude = _string_list(
        raw_replay.get("sourceExclude", []),
        "check.replay.sourceExclude",
    )
    shared_paths = _string_list(
        raw_replay.get("sharedPaths", []),
        "check.replay.sharedPaths",
    )
    for index, value in enumerate((*source_include, *source_exclude, *shared_paths)):
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise CheckConfigError(f"check.replay path pattern must stay inside the project root: {value!r}")

    return CheckConfig(
        root=root,
        policy_path=(root / policy_rel).resolve(),
        suite_order=suite_order,
        guards=tuple(guards),
        dependencies=tuple(dependencies),
        commands=commands,
        ignore=ignore,
        certification=CertificationConfig(
            profile=certification_profile,
            reusable_suites=reusable_suites,
            global_inputs=global_inputs,
            evidence_path=evidence_path,
        ),
        replay=ReplayConfig(
            source_include=source_include,
            source_exclude=source_exclude,
            shared_paths=shared_paths,
        ),
    )
